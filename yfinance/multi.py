#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# yfinance - market data downloader
# https://github.com/ranaroussi/yfinance
#
# Copyright 2017-2019 Ran Aroussi
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

from __future__ import print_function

import logging
import time as _time
import multitasking as _multitasking
import pandas as _pd

from . import Ticker, utils
from . import shared

logger = logging.getLogger(__name__)

def download(tickers, start=None, end=None, actions=False, threads=True, ignore_tz=None,
             group_by='column', 
             auto_adjust=None, back_adjust=None, 
             div_adjust=False,
             repair=False, keepna=False,
             progress=True, period="max", show_errors=True, interval="1d", prepost=False,
             proxy=None, rounding=False, timeout=10):
    """Download yahoo tickers
    :Parameters:
        tickers : str, list
            List of tickers to download
        period : str
            Valid periods: 1d,5d,1mo,3mo,6mo,1y,2y,5y,10y,ytd,max
            Either Use period parameter or use start and end
        interval : str
            Valid intervals: 1m,2m,5m,15m,30m,60m,90m,1h,1d,5d,1wk,1mo,3mo
            Intraday data cannot extend last 60 days
        start: str
            Download start date string (YYYY-MM-DD) or _datetime.
            Default is 1900-01-01
        end: str
            Download end date string (YYYY-MM-DD) or _datetime.
            Default is now
        group_by : str
            Group by 'ticker' or 'column' (default)
        prepost : bool
            Include Pre and Post market data in results?
            Default is False
            div_adjust: bool
                Dividend-adjust all OHLC data? Default is False
        repair: bool
            Detect currency unit 100x mixups and attempt repair
            Default is False
        keepna: bool
            Keep NaN rows returned by Yahoo?
            Default is False
        actions: bool
            Download dividend + stock splits data. Default is False
        threads: bool / int
            How many threads to use for mass downloading. Default is True
        ignore_tz: bool
            When combining from different timezones, ignore that part of datetime.
            Default depends on interval. Intraday = False. Day+ = True.
        proxy: str
            Optional. Proxy server URL scheme. Default is None
        rounding: bool
            Optional. Round values to 2 decimal places?
        show_errors: bool
            Optional. Doesn't print errors if False
        timeout: None or float
            If not None stops waiting for a response after given number of
            seconds. (Can also be a fraction of a second e.g. 0.01)
    """

    # Handle deprecated arguments first
    if auto_adjust is not None:
        print("WARNING: 'auto_adjust' is deprecated, switch to 'div_adjust' instead")
        div_adjust = auto_adjust
        auto_adjust = None
    if back_adjust is not None:
        print("WARNING: 'back_adjust' is deprecated, it was nonsense")
        back_adjust = None

    if ignore_tz is None:
        # Set default value depending on interval
        if interval[1:] in ['m', 'h']:
            # Intraday
            ignore_tz = False
        else:
            ignore_tz = True

    # create ticker list
    tickers = tickers if isinstance(
        tickers, (list, set, tuple)) else tickers.replace(',', ' ').split()

    # accept isin as ticker
    shared._ISINS = {}
    _tickers_ = []
    for ticker in tickers:
        if utils.is_isin(ticker):
            isin = ticker
            ticker = utils.get_ticker_by_isin(ticker, proxy)
            shared._ISINS[ticker] = isin
        _tickers_.append(ticker)

    tickers = _tickers_

    tickers = list(set([ticker.upper() for ticker in tickers]))

    if progress:
        shared._PROGRESS_BAR = utils.ProgressBar(len(tickers), 'completed')

    # reset shared._DFS
    shared._DFS = {}
    shared._ERRORS = {}

    # download using threads
    if threads:
        if threads is True:
            threads = min([len(tickers), _multitasking.cpu_count() * 2])
        _multitasking.set_max_threads(threads)
        for i, ticker in enumerate(tickers):
            _download_one_threaded(ticker, period=period, interval=interval,
                                   start=start, end=end, prepost=prepost,
                                   actions=actions, div_adjust=div_adjust,
                                   repair=repair, keepna=keepna,
                                   progress=(progress and i > 0), proxy=proxy,
                                   rounding=rounding, timeout=timeout)
        while len(shared._DFS) < len(tickers):
            _time.sleep(0.01)

    # download synchronously
    else:
        for i, ticker in enumerate(tickers):
            data = _download_one(ticker, period=period, interval=interval,
                                 start=start, end=end, prepost=prepost,
                                 actions=actions, div_adjust=div_adjust, 
                                 repair=repair, keepna=keepna,
                                 proxy=proxy,
                                 rounding=rounding, timeout=timeout)
            shared._DFS[ticker.upper()] = data
            if progress:
                shared._PROGRESS_BAR.animate()

    if progress:
        shared._PROGRESS_BAR.completed()

    if shared._ERRORS:
        if show_errors:
            print('\n%.f Failed download%s:' % (
                len(shared._ERRORS), 's' if len(shared._ERRORS) > 1 else ''))
            # print(shared._ERRORS)
            print("\n".join(['- %s: %s' %
                             v for v in list(shared._ERRORS.items())]))
        else:
            logger.error('%d failed downloads: %s',
                         len(shared._ERRORS), shared._ERRORS)

    if ignore_tz:
        for tkr in shared._DFS.keys():
            if (shared._DFS[tkr] is not None) and (shared._DFS[tkr].shape[0] > 0):
                shared._DFS[tkr].index = shared._DFS[tkr].index.tz_localize(None)

    if len(tickers) == 1:
        ticker = tickers[0]
        return shared._DFS[shared._ISINS.get(ticker, ticker)]

    try:
        data = _pd.concat(shared._DFS.values(), axis=1, sort=True,
                          keys=shared._DFS.keys())
    except Exception:
        _realign_dfs()
        data = _pd.concat(shared._DFS.values(), axis=1, sort=True,
                          keys=shared._DFS.keys())

    # switch names back to isins if applicable
    data.rename(columns=shared._ISINS, inplace=True)

    if group_by == 'column':
        data.columns = data.columns.swaplevel(0, 1)
        data.sort_index(level=0, axis=1, inplace=True)

    return data


def _realign_dfs():
    idx_len = 0
    idx = None

    for df in shared._DFS.values():
        if len(df) > idx_len:
            idx_len = len(df)
            idx = df.index

    for key in shared._DFS.keys():
        try:
            shared._DFS[key] = _pd.DataFrame(
                index=idx, data=shared._DFS[key]).drop_duplicates()
        except Exception:
            shared._DFS[key] = _pd.concat([
                utils.empty_df(idx), shared._DFS[key].dropna()
            ], axis=0, sort=True)

        # remove duplicate index
        shared._DFS[key] = shared._DFS[key].loc[
            ~shared._DFS[key].index.duplicated(keep='last')]


@_multitasking.task
def _download_one_threaded(ticker, start=None, end=None,
                           div_adjust=False, repair=False,
                           actions=False, progress=True, period="max",
                           interval="1d", prepost=False, proxy=None,
                           keepna=False, rounding=False, timeout=10):
    try:
        data = _download_one(ticker, start, end, div_adjust, repair,
                             actions, period, interval, prepost, proxy, rounding,
                             keepna, timeout)
    except Exception as e:
        # glob try/except needed as current thead implementation breaks if exception is raised.
        shared._DFS[ticker] = utils.empty_df()
        shared._ERRORS[ticker] = repr(e)
    else:
        shared._DFS[ticker.upper()] = data
    if progress:
        shared._PROGRESS_BAR.animate()


def _download_one(ticker, start=None, end=None,
                  div_adjust=False, repair=False,
                  actions=False, period="max", interval="1d",
                  prepost=False, proxy=None, rounding=False,
                  keepna=False, timeout=10):
    return Ticker(ticker).history(
        period=period, interval=interval,
        start=start, end=end, prepost=prepost,
        actions=actions, 
        div_adjust=div_adjust, repair=repair, proxy=proxy,
        rounding=rounding, keepna=keepna, timeout=timeout,
        debug=False, raise_errors=False  # debug and raise_errors false to not log and raise errors in threads
    )
