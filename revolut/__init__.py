# -*- coding: utf-8 -*-
"""
This package allows you to communicate with your Revolut accounts.
"""

# one-liner for finding current production client-version (requires GNU grep):
#  $ d='https://app.revolut.com' && curl -fsS "$d/start" \
#    | grep -Eo '/static/js/main\.\w+\.js' \
#    | xargs -I '{}' curl -fsS "$d{}" \
#    | grep -Po '\.ClientVersion,"\K\d+(\.\d+)?'
#
# other api endpoints:
#  - https://app.revolut.com/api/retail/user/current/accounts  - gives you all external accounts you've done business with


import math
from datetime import datetime
import time
from getpass import getpass
import json
import requests
from urllib.parse import urljoin
import os
import sys
import uuid
from ppretty import ppretty
from appdirs import user_config_dir
from pathlib import Path
from retry_decorator import retry
from exceptions import TokenExpiredException, ApiChangedException

# note the version is managed by zest.releaser
__version__ = '0.1.4'

API_ROOT = "https://app.revolut.com"
API_BASE = API_ROOT + "/api/retail"
_URL_GET_ACCOUNTS = API_BASE + "/user/current/wallet"
_URL_GET_OPPOSITE_ACCOUNTS = API_BASE + "/user/current/accounts"
_URL_GET_TRANSACTIONS_LAST = API_BASE + "/user/current/transactions/last"
# example for given account/wallet/pocket:  /api/retail/user/current/transactions/last?to=1636294208032&count=50&internalPocketId=8b0ccb11-3d99-4318-a4e8-fecef8ae3798
_URL_QUOTE = API_BASE + "/quote/"
_URL_EXCHANGE = API_BASE + "/exchange"


# TODO: rename to _SUPPORTED_CURRENCIES
_AVAILABLE_CURRENCIES = ['USD', 'RON', 'HUF', 'CZK', 'GBP', 'CAD', 'THB',
                         'SGD', 'CHF', 'AUD', 'ILS', 'DKK', 'PLN', 'MAD',
                         'AED', 'EUR', 'JPY', 'ZAR', 'NZD', 'HKD', 'TRY',
                         'QAR', 'NOK', 'SEK', 'BTC', 'ETH', 'XRP', 'BCH',
                         'LTC', 'SAR', 'RUB', 'RSD', 'MXN', 'ISK', 'HRK',
                         'BGN', 'XAU', 'IDR', 'INR', 'MYR', 'PHP', 'XLM',
                         'EOS', 'OMG', 'XTZ', 'ZRX']

_VAULT_ACCOUNT_TYPE = "SAVINGS"
_ACTIVE_ACCOUNT = "ACTIVE"
_TRANSACTION_COMPLETED = "COMPLETED"
_TRANSACTION_FAILED = "FAILED"
_TRANSACTION_PENDING = "PENDING"
_TRANSACTION_REVERTED = "REVERTED"
_TRANSACTION_DECLINED = "DECLINED"

_SUPPORTED_CHANNELS = ['EMAIL', 'SMS', 'APP']
_DEFAULT_CHANNEL = 'EMAIL'

# The amounts are stored as integer on Revolut.
# They apply a scale factor depending on the currency
_DEFAULT_SCALE_FACTOR = 100
_SCALE_FACTOR_CURRENCY_DICT = {
                                "EUR": 100,
                                "BTC": 100000000,
                                "ETH": 100000000,
                                "BCH": 100000000,
                                "XRP": 100000000,
                                "LTC": 100000000,
                               }


def _read_dict_from_file(file_loc) -> dict:
    try:
        with open(file_loc, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _load_config(conf_file, conf=None) -> dict:
    """Load json config file
    """
    if not conf:
        conf = {
            'phone': None,
            'pass': None,
            'token': None,
            'expiry': 0,
            'device': None,
            'channel': _DEFAULT_CHANNEL,
            'userAgent': 'Mozilla/5.0 (X11; Linux x86_64; rv:87.0) Gecko/20100101 Firefox/87.0',
            'requestTimeout': 5.0,
            'clientVer': '100.0',
            'selfie': None,
            'persistedKeys': ['token', 'expiry', 'device'],  # conf items stored under per-account conf file (file-name defined under 'accConf' key)
            '2FAProvider': None,
            'interactive': False,
            'commonConf': None,
            'accConf': None,
            'debug': None,
            'app2FASleepLoopSec': 3.5,
            '3FASleepLoopSec': 2.1
        }

    conf.update(_read_dict_from_file(conf_file))
    return conf


def init_root_conf_dir(d) -> str:
    if d and type(d) == str and os.path.isdir(d):
        pass
    elif 'REVOLUT_CONF_DIR' in os.environ:
        d = os.environ.get('REVOLUT_CONF_DIR')
    else:
        d = user_config_dir('revolut-py')

    Path(d).mkdir(parents=True, exist_ok=True)
    return d


class Amount:
    """ Class to handle the Revolut amount with currencies """
    def __init__(self, currency, revolut_amount=None, real_amount=None, revolut_fee=None, real_fee=None, tx=None):
        if currency not in _AVAILABLE_CURRENCIES:
            raise KeyError(currency)

        self.real_fee = 0.0
        self.revolut_fee = 0
        self.currency = currency
        self.scale = _SCALE_FACTOR_CURRENCY_DICT.get(
                self.currency, _DEFAULT_SCALE_FACTOR)

        if revolut_amount is not None:
            if type(revolut_amount) != int:
                raise TypeError(type(revolut_amount))
            elif real_amount is not None or real_fee is not None:
                raise ValueError("if revolut_amount is defined, then {real_amount,real_fee} cannot")
            self.revolut_amount = revolut_amount
            self.revolut_amount_total = revolut_amount
            self.real_amount = self._get_real_amount(revolut_amount)
            self.real_amount_total = self.real_amount
            if revolut_fee is not None and revolut_fee != 0:
                if revolut_fee < 0:
                    raise ValueError("revolut_fee = [{}] - shouldn't be negative".format(revolut_fee))
                self.revolut_fee = revolut_fee
                self.real_fee = self._get_real_amount(revolut_fee)
                self.revolut_amount_total = revolut_amount - revolut_fee
                self.real_amount_total = self._get_real_amount(self.revolut_amount_total)

        elif real_amount is not None:
            if not isinstance(real_amount, (float, int)):
                raise TypeError(type(real_amount))
            elif revolut_amount is not None or revolut_fee is not None:
                raise ValueError("if real_amount is defined, then {revolut_amount,revolut_fee} cannot")
            self.real_amount = float(real_amount)
            self.real_amount_total = self.real_amount
            self.revolut_amount = self._get_revolut_amount(self.real_amount)
            self.revolut_amount_total = self.revolut_amount
            if real_fee is not None and real_fee != 0:
                if real_fee < 0:
                    raise ValueError("real_fee = [{}] - shouldn't be negative".format(real_fee))
                self.real_fee = real_fee
                self.revolut_fee = self._get_revolut_amount(real_fee)
                self.revolut_amount_total = self.revolut_amount - self.revolut_fee
                self.real_amount_total = self._get_real_amount(self.revolut_amount_total)
        else:
            # print(json.dumps(
                    # tx,
                    # indent=4,
                    # separators=(',', ': '),
                    # ensure_ascii=False))
            raise ValueError("revolut_amount OR real_amount must be set")

        self.real_amount_str = self._get_real_amount_str(self.real_amount)
        self.real_fee_str = self._get_real_amount_str(self.real_fee)

    def _get_real_amount_str(self, real_amount) -> str:
        """ Get the real amount with the proper format, without currency """
        digits_after_float = int(math.log10(self.scale))
        return("%.*f" % (digits_after_float, real_amount))

    def __str__(self):
        return('{} {}'.format(self.real_amount_str, self.currency))

    def __repr__(self):
        return("Amount(real_amount={}, currency='{}')".format(
            self.real_amount, self.currency))

    def _get_real_amount(self, revolut_amount) -> float:
        """ Resolve the real amount from a Revolut amount
        >>> a = Amount()
        >>> a._get_real_amount(100)
        1.0
        """
        return float(revolut_amount / self.scale)

    def _get_revolut_amount(self, real_amount) -> int:
        """ Get the Revolut amount from a real amount
        >>> a = Amount()
        >>> a._get_revolut_amount(13.45)
        1345
        """
        return int(real_amount * self.scale)


class Merchant:
    """ Class to handle/represent the Revolut merchant """
    def __init__(self, id, merchant_id, scheme, name, mcc, category, city,
                 country, address, state):
        self.id = id
        self.merchant_id = merchant_id
        self.scheme = scheme
        self.name = name
        self.mcc = mcc
        self.category = category
        self.city = city
        self.country = country
        self.address = address
        self.state = state

    def __str__(self):
        print(ppretty(self, indent='    ', width=40, seq_length=10,
                      show_protected=True, show_static=True, show_properties=True, show_address=False))


class Counterpart:
    """ Class to handle/represent the Revolut transaction Counterpart
        Note the account is optional, eg for card payments/online payments you wont get it """
    def __init__(self, amount, account):
        self.amount = amount
        self.account = account  # optional; believe it's only defined when EXCHANGEing between currencies on our own Revolut account

    def __str__(self):
        print(ppretty(self, indent='    ', width=40, seq_length=10,
                      show_protected=True, show_static=True, show_properties=True, show_address=False))


# think all the fields are optional!!! ie you might have 'id', or a 'name'
class Beneficiary:
    def __init__(self, id, name, phone, country):
        self.id = id
        self.name = name
        self.phone = phone
        self.country = country

    def __str__(self):
        print(ppretty(self, indent='    ', width=40, seq_length=10,
                      show_protected=True, show_static=True, show_properties=True, show_address=False))


class SenderRecipient:
    """ Class to handle/represent the Revolut transaction Recipient or Sender """
    def __init__(self, id, first_name, last_name, country, code, username, account):
        self.id = id
        self.first_name = first_name
        self.last_name = last_name
        self.country = country
        self.code = code
        self.username = username
        self.account = account  # optional; only present if we're dealing with 'recipient'

    def __str__(self):
        print(ppretty(self, indent='    ', width=40, seq_length=10,
                      show_protected=True, show_static=True, show_properties=True, show_address=False))


class CounterpartAccount:
    """ Class to handle/represent the Revolut transaction counterpart """
    def __init__(self, id, type_, currency, bank_country, company_name,
                 first_name, last_name, payment_type, iban, bic, account_raw):
        self.id = id
        self.type = type_
        self.currency = currency
        self.bank_country = bank_country
        self.company_name = company_name
        self.first_name = first_name
        self.last_name = last_name
        self.payment_type = payment_type
        self.iban = iban  # likely only present if type = 'IBAN', but haven't confirmed
        self.bic = bic  # likely only present if type = 'IBAN', but haven't confirmed
        self.account_raw = account_raw  # the 'account'-keyed json object from server response

    def __str__(self):
        print(ppretty(self, indent='    ', width=40, seq_length=10,
                      show_protected=True, show_static=True, show_properties=True, show_address=False))


class CounterpartAccounts:
    """ Class to handle the Revolut counterpart accounts.
    """

    def __init__(self, ext_accounts):
        self.raw_list = ext_accounts  # as received from Revolut api

        self.list = [
            CounterpartAccount(
                id=account.get('id'),
                type_=account.get('type'),  # eg 'IBAN'
                currency=account.get('currency'),
                bank_country=account.get('bankCountry'),  # eg 'DE'
                company_name=account.get('companyName'),  # if company
                first_name=account.get('firstName'),      # if non-company
                last_name=account.get('lastName'),        # if non-company
                payment_type=account.get('paymentType'),  # eg 'regular'
                iban=account.get('account').get('IBAN'),
                bic=account.get('account').get('BIC'),
                account_raw=account.get('account')
            )
            for account in ext_accounts
        ]

    # def get_account_by_name(self, account_name):
        # """ Get an account by its name """
        # for account in self.list:
            # if account.name == account_name:
                # return account

    def __bool__(self):
        return bool(self.list)

    def __len__(self):
        return len(self.list)

    def __getitem__(self, key):
        """ Method to access the object as a list
        (ex : accounts[1]) """
        return self.list[key]

    def __iter__(self):
        for x in self.list:
            yield x


class Card:
    """ Class to handle/represent the Revolut card """
    def __init__(self, id, last_four, label):
        self.id = id
        self.last_four = last_four
        self.label = label

    def __str__(self):
        print(ppretty(self, indent='    ', width=40, seq_length=10,
                      show_protected=True, show_static=True, show_properties=True, show_address=False))


# TODO: rename to ExchangeTransaction? we already have an AccountTransaction
class Transaction:
    """ Class to handle an exchange transaction """
    def __init__(self, from_amount, to_amount, date):
        if type(from_amount) != Amount:
            raise TypeError
        elif type(to_amount) != Amount:
            raise TypeError
        elif type(date) != datetime:
            raise TypeError
        self.from_amount = from_amount
        self.to_amount = to_amount
        self.date = date

    def __str__(self):
        return('({}) {} => {}'.format(self.date.strftime("%d/%m/%Y %H:%M:%S"),
                                      self.from_amount,
                                      self.to_amount))


class Client:
    """ Do the requests with the Revolut servers """
    def __init__(self, conf, token=None, renew_token=True):
        self.conf = conf
        self.session = requests.session()
        self.renew_token = renew_token
        self.token = token
        self.auth = bool(token)

    def _configure_session(self) -> None:
        self.session.timeout = self.conf['requestTimeout']
        self.session.headers = {
                    'Host': 'app.revolut.com',
                    'X-Client-Version': self.conf['clientVer'],
                    'Origin': API_ROOT,
                    'Referer': API_ROOT + '/login',
                    'X-Device-Id': self.conf['device'],
                    'x-browser-application': 'WEB_CLIENT',
                    'User-Agent': self.conf['userAgent'],
                    "Content-Type": "application/json;charset=utf-8",
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Accept-Language": "en-US,en;q=0.6",
                    "Connection": "keep-alive",
                    'Sec-GPC': '1',
                    'DNT': '1',
                    'TE': 'Trailers',
                    }

        if self.auth:
            token = self.conf.get('token') if self.renew_token else self.token
            self.session.headers.update({'x-api-authorization': 'Basic ' + token})
        if self.conf.get('geo'):
            self.session.headers.update({'x-client-geo-location': self.conf.get('geo')})

    def _verif_resp_code(self, ret, codes) -> bool:
        if type(codes) == int:
            return ret.status_code == codes
        return ret.status_code in codes

    def _token_expired(self, ret) -> bool:
        try:
            j = ret.json()
        except:
            return False  # no valid json response, likely some other issue than token expiration
        # print('checking if expired response....{}, code key in reponse: {}'.format(ret.status_code, 'code' in j))

        # TODO: unsure whether we should just use 401 return code, or also read the 'code' to determine whether to retry?
        # return ret.status_code == 401 and j.get('code') == 9039
        return ret.status_code == 401

    def _make_call(self, method, f, url, expected_status_code, **kwargs) -> requests.Response:
        # print(' !!! EXEing {} make_call()'.format(method))
        self._configure_session()
        if 'files' in kwargs and 'Content-Type' in self.session.headers:
            # see https://stackoverflow.com/questions/12385179/how-to-send-a-multipart-form-data-with-requests-in-python#comment90652412_12385661 as to why
            del self.session.headers['Content-Type']
        ret = f(url=url, **kwargs)

        if self._verif_resp_code(ret, expected_status_code):
            return ret
        elif self.renew_token and self._token_expired(ret):
            # print('throwing TEEx...')
            raise TokenExpiredException()

        if type(expected_status_code) == int or len(expected_status_code) == 1:
            msg = expected_status_code
        else:
            msg = 'any of {}'.format(expected_status_code)

        raise ConnectionError(
            'Status code {} for {} {} (expected {})\n{}'.format(
                ret.status_code, method, url, msg, ret.text))

    def _make_call_retried(self) -> requests.Response:
        cb = {
            TokenExpiredException: lambda: renew_token(self.conf)
        }
        return retry(tries=2, callback_by_exception=cb)(self._make_call)

    def _get(self, url, *, expected_status_code=200, **kwargs) -> requests.Response:
        # print('!!!! going to GET for {}...'.format(url))
        return self._make_call_retried()(
            'GET', self.session.get, url, expected_status_code, **kwargs)

    def _post(self, url, *, expected_status_code=200, **kwargs) -> requests.Response:
        # print('!!!! going to POST for {}...'.format(url))
        return self._make_call_retried()(
            'POST', self.session.post, url, expected_status_code, **kwargs)

        ## DEBUG:
        # print('--------------------')
        # print('request-reponse dump:')
        # if 'files' not in kwargs:
            # from requests_toolbelt.utils import dump
            # d = dump.dump_all(ret)
            # print(d.decode('utf-8'))
            # print('--------------------')

        # print('!!reponse for {}:'.format(url))
        # print(ret)
        # print('!!reponse code:')
        # print(ret.status_code)
        # print('reponse txt:')
        # print(ret.text)
        # print('--------------------')


# TODO: make conf/cache scope local; allow passing in optional config dict? unsure how to handle persisting then
#       - create a Cache/Config class?
class Revolut:
    def __init__(self, device_id=None, token=None,
                 password=None, phone=None, channel=None,
                 persisted_keys=None, provider_2fa=None,
                 debug=False, interactive=None, root_conf_dir=None):
        root_conf_dir = init_root_conf_dir(root_conf_dir)
        common_conf_file = os.path.join(root_conf_dir, 'config')
        conf = _load_config(common_conf_file)
        self.conf = conf

        conf['commonConf'] = common_conf_file

        if type(interactive) == bool:
            conf['interactive'] = interactive
        else:
            conf['interactive'] = bool(getattr(sys, 'ps1', sys.flags.interactive))

        if conf.get('debug') is None:
            conf['debug'] = debug

        if phone:
            conf['phone'] = str(phone)
        elif not conf.get('phone'):
            if conf.get('interactive'):
                conf['phone'] = input(
                    "What is your mobile phone (used with your Revolut "
                    "account) [ex : +33612345678] ? ").strip()
            else:
                raise RuntimeError('no phone number provided')
        elif type(conf.get('phone')) != str:
            conf['phone'] = str(conf['phone'])

        # extend the config with account-specific config:
        account_conf_file = os.path.join(root_conf_dir, '{}.config'.format(conf.get('phone')))
        conf = _load_config(account_conf_file, conf)
        conf['accConf'] = account_conf_file

        if device_id:
            conf['device'] = device_id
        elif not conf.get('device'):
            conf['device'] = str(uuid.uuid4())  # verify format from real web client

        if callable(password):
            password = password(phone=conf['phone'])

        if password:
            conf['pass'] = str(password)
        elif not conf.get('pass'):
            if conf.get('interactive'):
                conf['pass'] = getpass(
                    "What is your Revolut app password [ex: 1234] ? ")
            else:
                raise RuntimeError('no password provided')
        elif type(conf.get('pass')) != str:
            conf['pass'] = str(conf['pass'])

        if channel:
            conf['channel'] = channel
        elif not conf.get('channel'):
            conf['channel'] = _DEFAULT_CHANNEL

        if conf['channel'] not in _SUPPORTED_CHANNELS:
            raise RuntimeError('provided/configured channel [{}] not supported'.format(conf['channel']))

        if provider_2fa is not None:
            if not callable(provider_2fa):
                raise TypeError('provider_2fa needs to be a callable when defined')
            conf['2FAProvider'] = provider_2fa
        elif channel in ['EMAIL', 'SMS'] and not conf.get('interactive'):
            raise RuntimeError('provider_2fa needs to be defined in non-interactive mode')

        if isinstance(persisted_keys, (list, tuple)):
            conf['persistedKeys'] = persisted_keys
        elif not isinstance(conf.get('persistedKeys'), (list, tuple)):
            raise TypeError('persistedKeys conf item not of type {list, tuple}')

        # keep token logic for last, as renewal will possibly persist token+other data:
        if token and type(token) == str:
            conf['token'] = token
        elif not conf.get('token') or conf.get('expiry', 0) - time.time() <= 5:
            # print('renewing token from Revolut init()')
            renew_token(conf)

        # self.logger.debug('effective config: {}'.format(conf))
        self.client = Client(conf=conf, token=True)

        # lazy-loaded fields:
        self.accounts = None
        self.external_accounts = None

    def get_external_accounts(self):
        if self.external_accounts is None:
            raw_external_accounts = self.client._get(_URL_GET_OPPOSITE_ACCOUNTS).json()
            if self.conf.get('debug'):
                print('DEBUG: Raw external accounts response from API:')
                print(json.dumps(
                        raw_external_accounts,
                        indent=4,
                        separators=(',', ': '),
                        ensure_ascii=False))

            self.external_accounts = CounterpartAccounts(raw_external_accounts)
        return self.external_accounts

    def get_accounts(self):
        """ Get the account balance for each currency
        and returns it as a dict {"balance":XXXX, "currency":XXXX} """

        if self.accounts is None:
            raw_accounts = self.client._get(_URL_GET_ACCOUNTS).json()

            if self.conf.get('debug'):
                print('DEBUG: Raw accounts response from API:')
                print(json.dumps(
                        raw_accounts,
                        indent=4,
                        separators=(',', ': '),
                        ensure_ascii=False))

            if 'pockets' not in raw_accounts:
                raise ApiChangedException('[pockets] key not in wallet response')

            account_balances = [a for a in raw_accounts.get('pockets')]
            self.accounts = Accounts(account_balances, raw_accounts)
        return self.accounts

    def get_account_transactions(self, from_date=None, to_date=None, from_legid=None, to_legid=None):
        """Get the account transactions for given timeframe."""

        def validate_and_set_date_param(arg, val):
            if val is None:
                return
            elif isinstance(val, datetime):
                params[arg] = int(val.timestamp() * 1000)
            elif isinstance(val, int):
                params[arg] = val
            else:
                raise TypeError('[{}] param cannot be of type {}'.format(arg, type(val)))

            expected_timestamp_len = 13
            if len(str(params[arg])) != expected_timestamp_len:
                raise ValueError('[{}] param should be millis with length of {}'.format(arg, expected_timestamp_len))

        params = {}
        # params.update({'count': 20})  # for debugging
        validate_and_set_date_param('to', to_date)
        validate_and_set_date_param('from', from_date)
        if from_legid and to_legid and from_legid == to_legid:
            raise ValueError('[from_legid] & [to_legid] cannot have same values')
        elif from_date and to_date and params['from'] > params['to']:
            raise ValueError('[from_date] cannot come after [to_date]')

        raw_transactions = []
        previous_timestamp = None  # track timestamps against pagination infinite-loop protection
        process = to_legid is None
        from_legid_found = to_legid_found = False
        ask_more = True
        while ask_more:
            # f = t = None
            # if from_date:
                # f = datetime.fromtimestamp(from_date.timestamp())
            # if 'to' in params:
                # t = datetime.fromtimestamp(params['to']/1000)
            # print('fetching tx data from [{}] to [{}]'.format(
                # f"{f:%Y-%m-%d %H:%M:%S}" if f else None,
                # f"{t:%Y-%m-%d %H:%M:%S}" if t else None))
            ret = self.client._get(_URL_GET_TRANSACTIONS_LAST, params=params)
            ret_transactions = ret.json()

            if self.conf.get('debug'):
                print('DEBUG: Raw TX response from API for params {}:'.format(params))
                print(json.dumps(
                        ret_transactions,
                        indent=4,
                        separators=(',', ': '),
                        ensure_ascii=False))

            if not ret_transactions:
                # print('empty response, break...')  # debug
                break
            params['to'] = ret_transactions[-1]['startedDate']  # 'to' arg needs to be modified for the pagination
            # i = datetime.fromtimestamp(ret_transactions[0]['startedDate']/1000)
            # j = datetime.fromtimestamp(ret_transactions[-1]['startedDate']/1000)
            # print('response; len [{}]; dates [0]{}  -  [-1]{}'.format(len(ret_transactions), f"{i:%Y-%m-%d %H:%M:%S}", f"{j:%Y-%m-%d %H:%M:%S}"))

            # attempt to safeguard against possible infitite loops:
            if previous_timestamp == params['to']:
                raise ApiChangedException('possible infinite loop detected in transaction query logic')
            previous_timestamp = params['to']

            for tx in ret_transactions:
                if to_legid is not None and not to_legid_found and tx['legId'] == to_legid:
                    process = to_legid_found = True
                    continue
                elif from_legid is not None and not from_legid_found and tx['legId'] == from_legid:
                    if to_legid is not None and not to_legid_found:
                        raise RuntimeError('[from_legid] encountered before [to_legid]; either API has changed or incorrect param(s) provided')
                    from_legid_found = True
                    ask_more = False
                    break
                elif process:
                    raw_transactions.append(tx)

        if (from_legid and not from_legid_found) or (to_legid and not to_legid_found):
            raise RuntimeError('[from_legid] and/or [to_legid] provided, but couldnt be located from Revolut API responses')

        # sanity; detect dupes:
        leg_ids = set([tx['legId'] for tx in raw_transactions])
        if len(leg_ids) != len(raw_transactions):
            raise ApiChangedException('received duplicate transactions w/ same [legId] attribute. verify if Revolut API has changed')

        # accounts = self.get_accounts()
        return AccountTransactions(raw_transactions)

    def get_wallet_id(self):
        """ Get the main wallet_id """
        ret = self.client._get(_URL_GET_ACCOUNTS)
        raw = ret.json()
        return raw.get('id')

    def quote(self, from_amount, to_currency):
        if type(from_amount) != Amount:
            raise TypeError("from_amount must be of the Amount type")
        elif to_currency not in _AVAILABLE_CURRENCIES:
            raise KeyError(to_currency)

        url_quote = urljoin(_URL_QUOTE, '{}{}?amount={}&side=SELL'.format(
            from_amount.currency,
            to_currency,
            from_amount.revolut_amount))
        ret = self.client._get(url_quote)
        raw_quote = ret.json()
        quote_obj = Amount(revolut_amount=raw_quote["to"]["amount"],
                           currency=to_currency)
        return quote_obj

    def exchange(self, from_amount, to_currency, simulate=False):
        if type(from_amount) != Amount:
            raise TypeError("from_amount must be of the Amount type")
        elif to_currency not in _AVAILABLE_CURRENCIES:
            raise KeyError(to_currency)

        data = {
            "fromCcy": from_amount.currency,
            "fromAmount": from_amount.revolut_amount,
            "toCcy": to_currency,
            "toAmount": None,
        }

        if simulate:
            # Because we don't want to exchange currencies
            # for every test ;)
            simu = '[{"account":{"id":"FAKE_ID"},\
            "amount":-1,"balance":0,"completedDate":123456789,\
            "counterpart":{"account":\
            {"id":"FAKE_ID"},\
            "amount":170,"currency":"BTC"},"currency":"EUR",\
            "description":"Exchanged to BTC","direction":"sell",\
            "fee":0,"id":"FAKE_ID",\
            "legId":"FAKE_ID","rate":0.0001751234,\
            "startedDate":123456789,"state":"COMPLETED","type":"EXCHANGE",\
            "updatedDate":123456789},\
            {"account":{"id":"FAKE_ID"},"amount":170,\
            "balance":12345,"completedDate":12345678,"counterpart":\
            {"account":{"id":"FAKE_ID"},\
            "amount":-1,"currency":"EUR"},"currency":"BTC",\
            "description":"Exchanged from EUR","direction":"buy","fee":0,\
            "id":"FAKE_ID",\
            "legId":"FAKE_ID",\
            "rate":5700.0012345,"startedDate":123456789,\
            "state":"COMPLETED","type":"EXCHANGE",\
            "updatedDate":123456789}]'
            raw_exchange = json.loads(simu)
        else:
            ret = self.client._post(_URL_EXCHANGE, json=data)
            raw_exchange = ret.json()

        # TODO: shouldn't we also validate raw_exchange length?
        if raw_exchange[0]["state"] == "COMPLETED":
            amount = raw_exchange[0]["counterpart"]["amount"]
            currency = raw_exchange[0]["counterpart"]["currency"]
            exchanged_amount = Amount(revolut_amount=amount,
                                      currency=currency)
            exchange_transaction = Transaction(from_amount=from_amount,
                                               to_amount=exchanged_amount,
                                               date=datetime.now())
        else:
            # TODO: is ConnectionError correct errtype here?
            raise ConnectionError("Transaction error : %s" % ret.text)

        return exchange_transaction


class Account:
    """ Class to handle an account.
        Also known as a 'Wallet' on Revolut side.
    """
    def __init__(self, id, account_type, balance, state, is_vault, vault_name):
        self.id = id
        self.account_type = account_type  # CURRENT, SAVINGS
        self.balance = balance  # of Amount type
        self.state = state  # ACTIVE, INACTIVE
        self.is_vault = is_vault
        self.vault_name = vault_name

        # convenience/derived members:
        self.currency = balance.currency  # for convenience - ie it's already covered in Amount object, but best have it also avail directly under Account
        self.name = self.build_account_name()

    def build_account_name(self):
        if self.account_type == _VAULT_ACCOUNT_TYPE:
            account_name = '{currency} {type} ({vault_name})'.format(
                    currency=self.balance.currency,
                    type=self.account_type,
                    vault_name=self.vault_name)
        else:
            account_name = '{currency} {type}'.format(
                    currency=self.balance.currency,
                    type=self.account_type)
        return account_name

    def __str__(self):
        return "{name} : {balance}".format(name=self.name,
                                           balance=str(self.balance))


class Accounts:
    """ Class to handle the Revolut accounts.
        Note Account is also known as Wallet in Revolut parlance.
    """

    def __init__(self, account_balances, raw_api_response):
        self.raw_list = account_balances  # as received from Revolut api, plus some additional field decorations
        self.raw_api_response = raw_api_response

        self.list = [
            Account(
                id=account.get('id'),
                account_type=account.get('type'),
                balance=Amount(
                    currency=account.get('currency'),
                    revolut_amount=account.get('balance'),
                ),
                state=account.get('state'),
                # custom, derived fields:
                is_vault=account.get('type') == _VAULT_ACCOUNT_TYPE,
                #is_vault=bool(account.get('name', False)),  # name is present when the account is a vault (type = SAVINGS)
                vault_name=account.get('name', '')  # name is present when the account is a vault (type = SAVINGS)
            )
            for account in account_balances
        ]

    def get_account_by_name(self, account_name):
        """ Get an account by its name """
        for account in self.list:
            if account.name == account_name:
                return account

    def __bool__(self):
        return bool(self.list)

    def __len__(self):
        return len(self.list)

    def __getitem__(self, key):
        """ Method to access the object as a list
        (ex : accounts[1]) """
        return self.list[key]

    def __iter__(self):
        for x in self.list:
            yield x

    def csv(self, lang="en"):
        lang_is_fr = lang == "fr"
        if lang_is_fr:
            csv_str = "Nom du compte;Solde;Devise"
        else:
            csv_str = "Account name,Balance,Currency"

        # Europe uses 'comma' as decimal separator,
        # so it can't be used as delimiter:
        delimiter = ";" if lang_is_fr else ","

        for account in self.list:
            if account.state == _ACTIVE_ACCOUNT:  # Do not print INACTIVE
                csv_str += "\n" + delimiter.join((
                    account.name,
                    account.balance.real_amount_str,
                    account.balance.currency,
                ))

        return csv_str.replace(".", ",") if lang_is_fr else csv_str


class AccountTransaction:
    """ Class to handle an account transaction """
    def __init__(
            self,
            id,
            leg_id,
            type_,
            state,
            started_date,
            created_date,
            updated_date,
            completed_date,
            amount,
            balance,
            description,
            comment,
            tag,
            category,
            account_id,
            counterpart,
            sender,
            recipient,
            beneficiary,
            merchant,
            card,
            is_ecommerce,
            is_vault):
        self.id = id
        self.leg_id = leg_id
        self.type = type_
        self.state = state
        self.started_date = started_date
        self.created_date = created_date
        self.updated_date = updated_date
        self.completed_date = completed_date  # can be None, eg if state = PENDING
        self.amount = amount
        self.balance = balance
        self.description = description.strip() if type(description) is str else description
        self.comment = comment.strip() if type(comment) is str else comment
        self.tag = tag
        self.category = category
        self.account_id = account_id
        self.counterpart = counterpart
        self.sender = sender
        self.recipient = recipient
        self.beneficiary = beneficiary  # when sending money (w/ description 'To <name>'), then we'll _either_ have recipient OR beneficiary, i think
        self.merchant = merchant
        self.card = card
        self.is_ecommerce = is_ecommerce
        self.is_vault = is_vault

        # convenience/derived members:
        self.currency = amount.currency  # for convenience - ie it's already covered in Amount object, but best have it also avail directly under Transaction
        self.opposing_id = self._get_opposing_id()

    def _get_opposing_id(self):
        """ Return id (either account of beneficiary/counterpart) for opposing party
            To be used for fast string-matching by downstream logic/library users """
        if self.recipient is not None:
            # TODO: note we assume recipient's "account" is always defined!
            return self.recipient.account
        elif self.merchant is not None:
            return self.merchant.merchant_id  # TODO: merchant_id is ok right?
        elif self.counterpart is not None and self.counterpart.account is not None:
            return self.counterpart.account
        elif self.beneficiary is not None:
            if self.beneficiary.id is not None:  # note this is _beneficiary_, not account id - right?
                return self.beneficiary.id
            else:
                # return self.beneficiary.name  # TODO: what to do: return name AND phone? think phone would make more sense for matching...
                return self.beneficiary.phone
        return None

    def __str__(self):
        return "{description}: {amount}".format(
            description=self.description,
            amount=str(self.amount)
        )

    def get_datetime__str(self, date_format="%d/%m/%Y %H:%M:%S"):
        """ 'Pending' transactions do not have 'completed_date' yet
        so return 'started_date' instead """
        timestamp = self.completed_date if self.completed_date \
                else self.started_date
        # Convert from timestamp to datetime
        dt = datetime.fromtimestamp(
            timestamp / 1000
        )
        dt_str = dt.strftime(date_format)
        return dt_str

    def get_description(self):
        # Adding 'pending' for processing transactions
        description = self.description
        if self.state == _TRANSACTION_PENDING:
            description = '{} **pending**'.format(description)
        return description

    def get_amount__str(self):
        """ Convert amount to float and return string representation """
        return str(self.amount.real_amount)

    def get_fee__str(self):
        """ Convert fee to float and return string representation """
        return str(self.amount.real_fee)


def _to_counterpart(o):
    """ believe 'account' key is only defined if tx type = EXCHANGE;
        if it only has [amount, currency] keys, then likely it's CARD_PAYMENT w/ merchant key in transaction avail.
    """
    if not o:
        return None

    account = o.get('account')
    if account is not None:
        account = account.get('id')

    return Counterpart(
        amount=Amount(revolut_amount=o.get('amount'),
                      currency=o.get('currency')),
        account=account
    )


def _to_balance(balance, currency):
    if balance is None:
        return None

    return Amount(revolut_amount=balance,
                  currency=currency)


def _to_sender_or_recipient(o):
    if not o:
        return None

    account = o.get('account')
    if account is not None:
        account = account.get('id')

    return SenderRecipient(
        id=o.get('id'),
        first_name=o.get('firstName'),
        last_name=o.get('lastName'),
        country=o.get('country'),
        code=o.get('code'),
        username=o.get('username'),
        account=account
    )


def _to_beneficiary(o):
    """ transfer to other external bank would have [id, country] fields,
        to other Revolut user would have [name, phone] fields """

    if not o:
        return None

    return Beneficiary(
        id=o.get('id'),
        name=o.get('name'),
        phone=o.get('phone'),
        country=o.get('country')
    )


def _to_merchant(o):
    if not o:
        return None

    return Merchant(
        id=o.get('id'),
        merchant_id=o.get('merchantId'),
        scheme=o.get('scheme'),
        name=o.get('name'),
        mcc=o.get('mcc'),
        category=o.get('category'),
        city=o.get('city'),
        country=o.get('country'),
        address=o.get('address'),
        state=o.get('state')
    )


def _to_card(o):
    if not o:
        return None

    return Card(
        id=o.get('id'),
        last_four=o.get('lastFour'),
        label=o.get('label')
    )


class AccountTransactions:
    """ Class to handle the account transactions """

    def __init__(self, account_transactions):
        self.raw_list = account_transactions  # as received from Revolut api
        self.list = [
            AccountTransaction(
                id=tx.get("id"),
                leg_id=tx.get("legId"),
                type_=tx.get("type"),
                state=tx.get("state"),
                started_date=tx.get("startedDate"),
                created_date=tx.get("createdDate"),
                updated_date=tx.get("updatedDate"),
                completed_date=tx.get("completedDate"),
                amount=Amount(revolut_amount=tx.get('amount'),
                              currency=tx.get('currency'),
                              revolut_fee=tx.get('fee')),
                balance=_to_balance(tx.get('balance'),
                                    tx.get('currency')),
                description=tx.get('description'),
                comment=tx.get('comment'),
                tag=tx.get('tag'),
                category=tx.get('category'),
                account_id=tx.get('account').get('id'),
                counterpart=_to_counterpart(tx.get('counterpart')),  # defined w/ EXCHANGE, CARD_PAYMENT
                sender=_to_sender_or_recipient(tx.get('sender')),  # w/ incoming TRANSFER; unsure if it's only applied if coming from other Revolut user
                recipient=_to_sender_or_recipient(tx.get('recipient')),  # w/ TRANSFER to other Revolut user; why not 'beneficiary' though? maybe recipient is for when sending to confirmed/whitelisted users?
                beneficiary=_to_beneficiary(tx.get('beneficiary')),  # defined w/ TRANSFER; eg bank transfer or transfer to other revolut user; actually - to other Revolut users those states tend to be in DELETED - what's up w/ that?
                merchant=_to_merchant(tx.get('merchant')),
                card=_to_card(tx.get('card')),  # defined w/ CARD_PAYMENT
                is_ecommerce=tx.get('eCommerce'),
                is_vault=bool(tx.get('vault'))
            )
            for tx in self.raw_list
        ]

    def __bool__(self):
        return bool(self.list)

    def __len__(self):
        return len(self.list)

    def __getitem__(self, key):
        """ Method to access the object as a list
        (ex : transactions[1]) """
        return self.list[key]

    def __iter__(self):
        for x in self.list:
            yield x

    def csv(self, lang="en", reverse=False):
        lang_is_fr = lang == "fr"
        if lang_is_fr:
            csv_str = "Date-heure (DD/MM/YYYY HH:MM:ss);Description;Montant;Frais;Devise"
            date_format = "%d/%m/%Y %H:%M:%S"
        else:
            csv_str = "Date-time (MM/DD/YYYY HH:MM:ss),Description,Amount,Fee,Currency"
            date_format = "%m/%d/%Y %H:%M:%S"

        # Europe uses 'comma' as decimal separator,
        # so it can't be used as delimiter:
        delimiter = ";" if lang_is_fr else ","

        # Do not export declined or failed payments
        transaction_list = list(reversed(self.list)) if reverse else self.list
        for account_transaction in transaction_list:
            if account_transaction.state not in [
                    _TRANSACTION_DECLINED,
                    _TRANSACTION_FAILED,
                    _TRANSACTION_REVERTED
            ]:
                csv_str += "\n" + delimiter.join((
                    account_transaction.get_datetime__str(date_format),
                    account_transaction.get_description(),
                    account_transaction.get_amount__str(),
                    account_transaction.get_fee__str(),
                    account_transaction.amount.currency
                ))
        return csv_str.replace(".", ",") if lang_is_fr else csv_str

from token_renewal import renew_token

