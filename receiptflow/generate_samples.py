"""
Generates synthetic, deliberately messy receipt images to benchmark the
extraction pipeline against, since no external receipt dataset can be
downloaded in this sandbox (huggingface.co / kaggle.com are network-blocked).

Each receipt uses a different vendor, layout, label vocabulary, date format,
font, and photo-noise profile -- this is the "non-template, messy receipt"
class that template-matching OCR (Dext, Ocrolus) handles poorly and that
this pipeline's extraction layer is built to handle instead.

Ground truth for every field is written to ground_truth.json so the
benchmark can score real accuracy, not a vibe.
"""
import json
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

random.seed(7)

OUT_DIR = Path(__file__).parent / "sample_data"
OUT_DIR.mkdir(exist_ok=True)

FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
]

# vendor -> true QBO category, used both to render receipts and to score
# the categorization rule engine later.
VENDORS = [
    ("Riverside Hardware Co", "Supplies & Materials"),
    ("Union Square Diner", "Meals & Entertainment"),
    ("QuikFuel Station #42", "Vehicle & Fuel"),
    ("Staples Office Center", "Office Supplies"),
    ("Blue Bottle Coffee", "Meals & Entertainment"),
    ("Ace Parking Garage", "Travel"),
    ("Home Depot", "Supplies & Materials"),
    ("Delta Airlines", "Travel"),
    ("Comcast Business", "Utilities"),
    ("Chevron", "Vehicle & Fuel"),
    ("Marriott Downtown", "Travel"),
    ("FedEx Office", "Shipping & Postage"),
    ("Whole Foods Market", "Meals & Entertainment"),
    ("AT&T Wireless", "Utilities"),
    ("Sherwin-Williams Paint", "Supplies & Materials"),
    ("Enterprise Rent-A-Car", "Travel"),
    ("PG&E", "Utilities"),
    ("Costco Wholesale #118", "Supplies & Materials"),
    ("Ace Hardware", "Supplies & Materials"),
    ("Uber Trip", "Travel"),
]

DATE_FORMATS = ["%m/%d/%Y", "%m/%d/%y", "%d-%m-%Y", "%b %d, %Y", "%Y-%m-%d", "%d %b %Y"]
TOTAL_LABELS = ["TOTAL", "Total Due", "AMOUNT DUE", "Grand Total", "Balance Due", "TOTAL:", "You Paid"]
TAX_LABELS = ["Sales Tax", "HST", "VAT", "Tax", "State Tax 8.25%"]

ITEM_POOL = [
    "Widget A", "Service Fee", "Item #3381", "Labor", "Parts", "Bag of misc.",
    "SKU-99213", "Consumable", "Delivery Charge", "Misc Item", "Product X",
]


def random_date():
    import datetime
    d = datetime.date(2026, random.randint(1, 8), random.randint(1, 28))
    fmt = random.choice(DATE_FORMATS)
    return d.strftime(fmt), d.isoformat()


def make_receipt(idx, vendor, category):
    font_path = random.choice(FONTS)
    width = random.randint(360, 460)
    height = random.randint(560, 760)
    img = Image.new("L", (width, height), color=random.randint(235, 255))
    draw = ImageDraw.Draw(img)

    def font(size):
        return ImageFont.truetype(font_path, size)

    y = 20
    draw.text((random.randint(10, 40), y), vendor.upper(), fill=0, font=font(random.randint(16, 20)))
    y += 30
    if random.random() < 0.5:
        draw.text((15, y), f"Store #{random.randint(100, 999)}  Tel: (555) {random.randint(200,999)}-{random.randint(1000,9999)}",
                   fill=0, font=font(11))
        y += 20

    date_str, date_iso = random_date()
    draw.text((15, y), f"Date: {date_str}" if random.random() < 0.5 else date_str, fill=0, font=font(12))
    y += 25

    if random.random() < 0.6:
        draw.text((15, y), f"Trans# {random.randint(100000,999999)}", fill=0, font=font(10))
        y += 20

    y += 10
    n_items = random.randint(2, 5)
    line_total = 0.0
    for _ in range(n_items):
        name = random.choice(ITEM_POOL)
        price = round(random.uniform(3.5, 89.0), 2)
        line_total += price
        draw.text((15, y), name, fill=0, font=font(11))
        draw.text((width - 90, y), f"${price:,.2f}", fill=0, font=font(11))
        y += 20

    y += 10
    draw.line((15, y, width - 15, y), fill=0, width=1)
    y += 12

    tax_rate = round(random.uniform(0.06, 0.095), 4)
    tax = round(line_total * tax_rate, 2)
    total = round(line_total + tax, 2)

    if random.random() < 0.85:
        draw.text((15, y), "Subtotal", fill=0, font=font(12))
        draw.text((width - 90, y), f"${line_total:,.2f}", fill=0, font=font(12))
        y += 20
        draw.text((15, y), random.choice(TAX_LABELS), fill=0, font=font(12))
        draw.text((width - 90, y), f"${tax:,.2f}", fill=0, font=font(12))
        y += 20
    else:
        # some receipts omit a clean subtotal/tax breakdown -- messier case
        total = line_total

    total_label = random.choice(TOTAL_LABELS)
    draw.text((15, y), total_label, fill=0, font=font(random.randint(14, 18)))
    draw.text((width - 95, y), f"${total:,.2f}", fill=0, font=font(random.randint(14, 18)))
    y += 30

    if random.random() < 0.4:
        draw.text((15, y), random.choice(["THANK YOU", "Have a nice day!", "Customer Copy", "Card ending 4471"]),
                   fill=0, font=font(10))

    # --- messiness: rotation, noise, blur, low contrast, crumple lines ---
    img = img.convert("RGB")
    if random.random() < 0.7:
        angle = random.uniform(-4, 4)
        img = img.rotate(angle, expand=True, fillcolor=(255, 255, 255))
    if random.random() < 0.5:
        img = img.filter(ImageFilter.GaussianBlur(radius=random.uniform(0.3, 1.1)))
    if random.random() < 0.4:
        d2 = ImageDraw.Draw(img)
        for _ in range(random.randint(2, 6)):
            x1, y1 = random.randint(0, img.width), random.randint(0, img.height)
            x2, y2 = x1 + random.randint(-40, 40), y1 + random.randint(-40, 40)
            d2.line((x1, y1, x2, y2), fill=(200, 200, 200), width=1)
    if random.random() < 0.3:
        import numpy as np
        arr = np.asarray(img).astype("int16")
        noise = np.random.normal(0, 8, arr.shape).astype("int16")
        arr = (arr + noise).clip(0, 255).astype("uint8")
        img = Image.fromarray(arr)

    fname = f"receipt_{idx:02d}.png"
    img.save(OUT_DIR / fname)
    return {
        "file": fname,
        "vendor": vendor,
        "true_category": category,
        "true_total": round(total, 2),
        "true_date": date_iso,
    }


def main():
    ground_truth = []
    picks = random.sample(VENDORS, k=len(VENDORS))
    for i, (vendor, category) in enumerate(picks, start=1):
        ground_truth.append(make_receipt(i, vendor, category))
    with open(OUT_DIR / "ground_truth.json", "w") as f:
        json.dump(ground_truth, f, indent=2)
    print(f"Generated {len(ground_truth)} synthetic receipts in {OUT_DIR}")


if __name__ == "__main__":
    main()
