"""Tesseract OCR wrapper -- the OSS OCR layer of the pipeline."""
import pytesseract
from PIL import Image, ImageOps, ImageFilter


def ocr_image(path) -> str:
    img = Image.open(path).convert("L")
    # 2x upscale + sharpen + autocontrast measurably reduces digit
    # misreads (e.g. "6" vs "5") on noisy/low-res phone-photo receipts --
    # a standard preprocessing step, not specific to this dataset
    img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    img = img.filter(ImageFilter.SHARPEN)
    img = ImageOps.autocontrast(img)
    return pytesseract.image_to_string(img, config="--psm 6")
