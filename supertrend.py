import ccxt 
from pprint import pprint
import config
import schedule
import pandas as pd
pd.set_option('display.max_rows', None)

import warnings
warnings.filterwarnings('ignore')
import pickle
import os

import numpy as np
from datetime import datetime
import time
import math
import threading

if config.IS_TESTNET:
    #testnet binance
    print('CCXT version:', ccxt.__version__)  # requires CCXT version > 1.20.31
    exchange = ccxt.binance({
        'apiKey': config.BINANCE_API_KEY_TEST,
        'secret': config.BINANCE_SECRET_KEY_TEST,
        'enableRateLimit': True,
        'options': {
            'defaultType': 'future', 
        },
    })
    exchange.set_sandbox_mode(True)
    #response = exchange.fapiPrivateGetPositionRisk()  # <<<<<<<<<<<<<<< changed for fapiPrivateGetPositionRisk here
    #pprint(response)
else:
    exchange_id = 'binance'
    exchange_class = getattr(ccxt, exchange_id)
    exchange = exchange_class({
        'apiKey': config.BINANCE_API_KEY_PROD,
        'secret': config.BINANCE_SECRET_KEY_PROD,
    })

markets = exchange.load_markets()

def get_market(list1):
    trade = []
    print('getting markets')
    for market in list1:
        if market.split('/')[1] == 'USDT':
            if markets[market]['info']['status'] == 'TRADING' and markets[market]['info']['isSpotTradingAllowed']: 
                  
                trade.append(market)
    return trade


def tr(data):
    data['previous_close'] = data['close'].shift(1)
    data['high-low'] = abs(data['high'] - data['low'])
    data['high-pc'] = abs(data['high'] - data['previous_close'])
    data['low-pc'] = abs(data['low'] - data['previous_close'])

    tr = data[['high-low', 'high-pc', 'low-pc']].max(axis=1)

    return tr

def atr(data, period=14):
    data['tr'] = tr(data)
    atr = data['tr'].rolling(period).mean()

    return atr


def supertrend(df, period=12, atr_multiplier=3, period2 = 10, atr_multiplier2 = 1,period3 = 11, atr_multiplier3 = 2 ):
    hl2 = (df['high'] + df['low']) / 2
    df['atr'] = atr(df, period)
    df['upperband'] = hl2 + (atr_multiplier * df['atr'])
    df['lowerband'] = hl2 - (atr_multiplier * df['atr'])

    df['atr2'] = atr(df, period2)
    df['upperband2'] = hl2 + (atr_multiplier2 * df['atr2'])
    df['lowerband2'] = hl2 - (atr_multiplier2 * df['atr2'])

    df['atr3'] = atr(df, period3)
    df['upperband3'] = hl2 + (atr_multiplier3 * df['atr3'])
    df['lowerband3'] = hl2 - (atr_multiplier3 * df['atr3'])
    df['in_uptrend'] = True
    df['in_uptrend2'] = True
    df['in_uptrend3'] = True
    df['uptrend'] = True
    df['ewma'] = df['close'].ewm(200, adjust=True).mean()

    for current in range(1, len(df.index)):
        previous = current - 1

        if df['close'][current] > df['upperband'][previous]:
            df['in_uptrend'][current] = True

        elif df['close'][current] < df['lowerband'][previous]:
            df['in_uptrend'][current] = False

        else:
            df['in_uptrend'][current] = df['in_uptrend'][previous]

            if df['in_uptrend'][current] and df['lowerband'][current] < df['lowerband'][previous]:
                df['lowerband'][current] = df['lowerband'][previous]
            if not df['in_uptrend'][current] and df['upperband'][current] > df['upperband'][previous]:
                df['upperband'][current] = df['upperband'][previous]

        if df['close'][current] > df['upperband2'][previous]:
            df['in_uptrend2'][current] = True

        elif df['close'][current] < df['lowerband2'][previous]:
            df['in_uptrend2'][current] = False

        else:
            df['in_uptrend2'][current] = df['in_uptrend2'][previous]

            if df['in_uptrend2'][current] and df['lowerband2'][current] < df['lowerband2'][previous]:
                df['lowerband2'][current] = df['lowerband2'][previous]
            if not df['in_uptrend2'][current] and df['upperband2'][current] > df['upperband2'][previous]:
                df['upperband2'][current] = df['upperband2'][previous]

        if df['close'][current] > df['upperband3'][previous]:
            df['in_uptrend3'][current] = True

        elif df['close'][current] < df['lowerband3'][previous]:
            df['in_uptrend3'][current] = False

        else:
            df['in_uptrend3'][current] = df['in_uptrend3'][previous]
                
            if df['in_uptrend3'][current] and df['lowerband3'][current] < df['lowerband3'][previous]:
                df['lowerband3'][current] = df['lowerband3'][previous]

            if not df['in_uptrend3'][current] and df['upperband3'][current] > df['upperband3'][previous]:
                df['upperband3'][current] = df['upperband3'][previous]
        
        if df['in_uptrend'][current] and df['in_uptrend2'][current] and df['in_uptrend3'][current]:
            df['uptrend'][current] = True
            
        else: 
            df['uptrend'][current] = False
    return df

def tradable_markets(list2):
    if not os.path.exists('trading.p'):
        print('no file creating...')
        with open('trading.p', 'wb') as fp:
            tradable_market = {}
            print('getting tradable markets')
            for market in list2:
                
                bars = exchange.fetch_ohlcv(market,timeframe='1h', limit=100)
                df = pd.DataFrame(bars[:-1], columns=['timestamp','open','high','low','close','volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        
                tr(df)
                
                atr(df)
                
                supertrend(df)
                    
                tradable_market[market] = {'info':df,'in position': False}
            pickle.dump(tradable_market, fp, protocol=pickle.HIGHEST_PROTOCOL)
    else:
        with open('trading.p', 'rb') as fp:
            print('Opening file')
            tradable_market = pickle.load(fp)
            for market in list2:
                
                bars = exchange.fetch_ohlcv(market,timeframe='1h', limit=100)
                df = pd.DataFrame(bars[:-1], columns=['timestamp','open','high','low','close','volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        
                tr(df)
                
                atr(df)
                
                supertrend(df)
                    
                tradable_market[market]['info'] = df
            
    return tradable_market

def tradings(dict1):
    if not os.path.exists('trading.p'):
        print('no file creating...')
        with open('trading.p', 'wb') as fp:
            trading = {}
            for k,v in dict1.items():
                last_row_index = len(v.index) - 1
                previous_row_index = last_row_index - 1
                if v['in_uptrend2'][last_row_index] and v['close'][last_row_index] > v['ewma'][last_row_index]:
                    trading[k] = [v, True]
            pickle.dump(trading, fp, protocol=pickle.HIGHEST_PROTOCOL)

    else:
        with open('trading.p', 'rb') as fp:
            print('Opening file')
            trading = pickle.load(fp)
    return trading


def remove_downtrend(dict2):
    

    for k,v in dict2.items(): 
        if v['in position']:
            last_row_index = len(v['info'].index) - 1
            previous_row_index = last_row_index - 1
            if v['info']['uptrend'][previous_row_index] and not v['info']['uptrend'][last_row_index] and v['in position']:
                
                sell_downtrend = exchange.fetch_balance()['free'][k.split('/')[0]]
                exchange.create_market_sell_order(k, sell_downtrend)
                v['in position'] = False
                print(f'{k} sold, is in a downtrend')
           
            


    return dict2

def length(dict3):
    length = len(dict3)
    return length

def new_trades(dict1):
    print('checking for new markets...')
    count = 0
    for k,v in dict1.items():
        
        last_row_index = len(v.index) - 1
        previous_row_index = last_row_index - 1       
        if not ['in_uptrend2'][previous_row_index] and v['in_uptrend2'][last_row_index] and v['close'][last_row_index] > v['ewma'][last_row_index]:
            dict1[k] = [v, True]
            count+=1
    if count == 1:
        print('1 market added')
    else:
        print(f'{count} markets added')
    return dict1




def round_decimals_down(number:float, decimals:int=2):
    """
    Returns a value rounded down to a specific number of decimal places.
    """
    if not isinstance(decimals, int):
        raise TypeError("decimal places must be an integer")
    elif decimals < 0:
        raise ValueError("decimal places has to be 0 or more")
    elif decimals == 0:
        return math.floor(number)

    factor = 10 ** decimals
    return math.floor(number * factor) / factor

def allocations(list1):
    total_cash = float(exchange.fetch_balance()['free']['USDT'])   
    for market in list1:
        total_cash += float(exchange.fetch_ticker(market)['info']['lastPrice']) * exchange.fetch_balance()['free'][market.split('/')[0]]
    
    allocation = total_cash/(len(list1) - 14)
    return allocation


def porfolio_management(dict2, allocation):
    print('checking for new markets...')
    for k,v in dict2.items():
        last_row_index = len(v['info'].index) - 1
        previous_row_index = last_row_index - 1  
        if not v['in position']:
            if not v['info']['uptrend'][previous_row_index] and v['info']['uptrend'][last_row_index] and v['info']['close'][last_row_index] > v['info']['ewma'][last_row_index]:
                current_amount = float(exchange.fetch_ticker(k)['info']['lastPrice']) * exchange.fetch_balance()['free'][k.split('/')[0]]    
                
                buy_amount = allocation / float(exchange.fetch_ticker(k)['info']['lastPrice'])
                exchange.create_market_buy_order(k, round(buy_amount,5))
                print(f'Bought {k}')
                v['in position'] = True

def while_trading_sell(dict3):
    print('while Trading selling')
    for k,v in dict3.items():
        current = len(v['info'].index) - 1    
        previous = current - 1
        previous_2 = current - 2
        next1 = current + 1
        

        if v['in position'] and v['info']['lowerband2'][current] == v['info']['lowerband2'][previous]:
            sell_downtrend = exchange.fetch_balance()['free'][k.split('/')[0]]
            exchange.create_market_sell_order(k, sell_downtrend)
            print(f'Sold {k}')
            v['in position'] = False


def while_trading_buy(dict3,allocation):
    print('while Trading buying')
    for k,v in dict3.items():
        current = len(v['info'].index) - 1    
        previous = current - 1
        previous_2 = current - 2
        next1 = current + 1

        if not v['in position'] and v['info']['lowerband2'][current] > v['info']['lowerband2'][previous] and v['info']['uptrend'][current] and v['info']['uptrend'][previous] and v['info']['close'][current] > v['info']['ewma'][current]:
            buy_amount = allocation / float(exchange.fetch_ticker(k)['info']['lastPrice'])
            exchange.create_market_buy_order(k, round(buy_amount,2))
            print(f'Bought {k}')
            v['in position'] = True
            
            

def while_trading(dict3,allocation):
    for k,v in dict3.items():
        current = len(v['info'].index) - 1    
        previous = current - 1
        previous_2 = current - 2
        next1 = current + 1
        

        if v['in position'] and v['info']['lowerband2'][current] == v['info']['lowerband2'][previous] and v['info']['lowerband2'][current] == v['info']['lowerband2'][previous_2]:
            sell_downtrend = exchange.fetch_balance()['free'][k.split('/')[0]]
            exchange.create_market_sell_order(k, sell_downtrend)
            print(f'Sold {k}')
            v['in position'] = False
            
    
                    
        elif not v['in position'] and v['info']['lowerband2'][current] > v['info']['lowerband2'][previous] and v['info']['in_uptrend2'][current] and v['info']['in_uptrend2'][previous] and v['info']['close'][current] > v['info']['ewma'][current]:
            buy_amount = allocation / float(exchange.fetch_ticker(k)['info']['lastPrice'])
            exchange.create_market_buy_order(k, round(buy_amount,2))
            print(f'Bought {k}')
            v['in position'] = True



def save_trading(dict1):

    with open('trading.p', 'wb') as fp:
        pickle.dump(dict1, fp, protocol=pickle.HIGHEST_PROTOCOL)
        print('saved! See you in an hour!')

def which_trades(dict1):
    list1 = []
    
    for k,v in dict1.items():
        if v['in position'] :
           list1.append(k) 

    if len(list1) == 0:
        print('We are trading nothing!')  
    else:
        print(f"We are trading {', '.join(list1)} right now!")

    def run_continuously(interval=1):
        """Continuously run, while executing pending jobs at each
        elapsed time interval.
        @return cease_continuous_run: threading. Event which can
        be set to cease continuous run. Please note that it is
        *intended behavior that run_continuously() does not run
        missed jobs*. For example, if you've registered a job that
        should run every minute and you set a continuous run
        interval of one hour then your job won't be run 60 times
        at each interval but only once.
        """
        cease_continuous_run = threading.Event()

        class ScheduleThread(threading.Thread):
            @classmethod
            def run(cls):
                while not cease_continuous_run.is_set():
                    schedule.run_pending()
                    time.sleep(interval)

        continuous_thread = ScheduleThread()
        continuous_thread.start()
        return cease_continuous_run


def run_bot():

    if config.IS_TESTNET:
        trade = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 
        'NEO/USDT', 'LTC/USDT', 'QTUM/USDT', 'ADA/USDT', 'XRP/USDT', 
        'EOS/USDT','LINK/USDT','VET/USDT','MATIC/USDT','DOGE/USDT','DOT/USDT', 
        'RSR/USDT','SHIB/USDT','ZIL/USDT', 'ZRX/USDT','ETC/USDT','BAKE/USDT',
        'SOL/USDT','THETA/USDT', 'ENJ/USDT','DASH/USDT','KSM/USDT','SUPER/USDT','SUSHI/USDT','XLM/USDT',
        'BADGER/USDT', 'CKB/USDT', 'ICP/USDT', 'IOTA/USDT', 'ALGO/USDT','MKR/USDT','BCH/USDT','SAND/USDT', 
        'CAKE/USDT','AAVE/USDT', 'KAVA/USDT', 'TFUEL/USDT', 'ONE/USDT', 'FIL/USDT', 'UNI/USDT', 'XMR/USDT', 'BAT/USDT','CTXC/USDT','GBP/USDT','BAR/USDT']
    else:        
        trade = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 
        'NEO/USDT', 'LTC/USDT', 'QTUM/USDT', 'ADA/USDT', 'XRP/USDT', 
        'EOS/USDT','LINK/USDT','VET/USDT','MATIC/USDT','DOGE/USDT','DOT/USDT', 
        'LUNA/USDT', 'RSR/USDT','SHIB/USDT','ZIL/USDT', 'ZRX/USDT','ETC/USDT','BAKE/USDT',
        'SOL/USDT','THETA/USDT', 'ENJ/USDT','DASH/USDT','KSM/USDT','SUPER/USDT','SUSHI/USDT','XLM/USDT',
        'BADGER/USDT', 'CKB/USDT', 'ICP/USDT', 'IOTA/USDT', 'ALGO/USDT','MKR/USDT','BCH/USDT','SAND/USDT', 
        'CAKE/USDT','AAVE/USDT', 'KAVA/USDT', 'TFUEL/USDT', 'ONE/USDT', 'FIL/USDT', 'UNI/USDT', 'XMR/USDT', 'BAT/USDT','CTXC/USDT','GBP/USDT','BAR/USDT']

    tradable_market = tradable_markets(trade)

    #trading = tradings(tradable_market)

    trading1 = remove_downtrend(tradable_market)

    #trading_length = length(trading1)

    #new_trading = new_trades(dict1=tradable_market, dict2=trading1)

    allocation = allocations(list1=trade)

    while_trading_sell(trading1)

    porfolio_management(trading1, allocation=allocation)

    while_trading_buy(trading1,allocation=allocation)

    #while_trading(trading1,allocation=allocation)

    save_trading(trading1)

    which_trades(trading1)
    
    return trading1, allocation
   



schedule.every().hour.at(":00").do(run_bot)
#schedule.every(1).minutes.do(run_bot)


while True:
    schedule.run_pending()
    time.sleep(1)