# -*- coding: utf-8 -*-
"""
This package allows you to communicate with your Revolut accounts
"""

# one-liner for finding current production client-version (requires GNU grep):
#  $ d='https://app.revolut.com' && curl -fsS "$d/start" \
#    | grep -Eo '/static/js/main\.\w+\.chunk\.js' \
#    | xargs -I '{}' curl -fsS "$d{}" \
#    | grep -Po '\.ClientVersion,"\K\d+\.\d+(?=")'


import math
from datetime import datetime
import time
from getpass import getpass
import json
import requests
from urllib.parse import urljoin
import os
import uuid
from appdirs import user_config_dir
from pathlib import Path
from retry_decorator import retry
from exceptions import TokenExpiredException, ApiChangedException

__version__ = '0.1.4'  # Should be the same in setup.py

API_ROOT = "https://app.revolut.com"
API_BASE = API_ROOT + "/api/retail"
_URL_GET_ACCOUNTS = API_BASE + "/user/current/wallet"
_URL_GET_TRANSACTIONS_LAST = API_BASE + "/user/current/transactions/last"
_URL_QUOTE = API_BASE + "/quote/"
_URL_EXCHANGE = API_BASE + "/exchange"


# TODO: rename to _SUPPORTED_CURRENCIES
_AVAILABLE_CURRENCIES = ["USD", "RON", "HUF", "CZK", "GBP", "CAD", "THB",
                         "SGD", "CHF", "AUD", "ILS", "DKK", "PLN", "MAD",
                         "AED", "EUR", "JPY", "ZAR", "NZD", "HKD", "TRY",
                         "QAR", "NOK", "SEK", "BTC", "ETH", "XRP", "BCH",
                         "LTC", "SAR", "RUB", "RSD", "MXN", "ISK", "HRK",
                         "BGN", "XAU", "IDR", "INR", "MYR", "PHP", "XLM",
                         "EOS", "OMG", "XTZ", "ZRX"]

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
    except Exception:
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
            'clientVer': '100.0',
            'selfie': None,
            'persistedKeys': ['token', 'expiry', 'device'],
            '2FAProvider': None,
            'interactive': False,
            'commonConf': None,
            'accConf': None,
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
    def __init__(self, currency, revolut_amount=None, real_amount=None):
        if currency not in _AVAILABLE_CURRENCIES:
            raise KeyError(currency)
        self.currency = currency

        self.scale = _SCALE_FACTOR_CURRENCY_DICT.get(
                self.currency, _DEFAULT_SCALE_FACTOR)

        if revolut_amount is not None:
            if type(revolut_amount) != int:
                raise TypeError(type(revolut_amount))
            self.revolut_amount = revolut_amount
            self.real_amount = self.get_real_amount()

        elif real_amount is not None:
            if not isinstance(real_amount, (float, int)):
                raise TypeError(type(real_amount))
            self.real_amount = float(real_amount)
            self.revolut_amount = self.get_revolut_amount()
        else:
            raise ValueError("revolut_amount or real_amount must be set")

        self.real_amount_str = self.get_real_amount_str()

    def get_real_amount_str(self) -> str:
        """ Get the real amount with the proper format, without currency """
        digits_after_float = int(math.log10(self.scale))
        return("%.*f" % (digits_after_float, self.real_amount))

    def __str__(self):
        return('{} {}'.format(self.real_amount_str, self.currency))

    def __repr__(self):
        return("Amount(real_amount={}, currency='{}')".format(
            self.real_amount, self.currency))

    def get_real_amount(self) -> float:
        """ Resolve the real amount from a Revolut amount
        >>> a = Amount(revolut_amount=100, currency="EUR")
        >>> a.get_real_amount()
        1.0
        """
        return float(self.revolut_amount / self.scale)

    def get_revolut_amount(self) -> int:
        """ Get the Revolut amount from a real amount
        >>> a = Amount(real_amount=1, currency="EUR")
        >>> a.get_revolut_amount()
        100
        """
        return int(self.real_amount * self.scale)


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
    def __init__(self, conf, token=False, renew_token=True):
        self.conf = conf
        self.session = requests.session()
        self.renew_token = renew_token
        self.token = token
        self.auth = bool(token)

    def _set_hdrs(self) -> None:
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
        if 'geo' in self.conf:
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
        return ret.status_code == 401 and 'code' in j and j['code'] == 9039

    def _make_call(self, method, f, url, expected_status_code, **kwargs) -> requests.Response:
        # print(' !!! EXEing {} make_call()'.format(method))
        self._set_hdrs()
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
    def __init__(self, device_id=None, token=None, password=None, phone=None, channel=None, persisted_keys=None, provider_2fa=None, interactive=None, root_conf_dir=None):
        root_conf_dir = init_root_conf_dir(root_conf_dir)
        common_conf_file = os.path.join(root_conf_dir, 'config')
        conf = _load_config(common_conf_file)
        conf['commonConf'] = common_conf_file
        if provider_2fa:
            if not callable(provider_2fa):
                raise TypeError('provider_2fa needs to be a callable when defined')
            conf['2FAProvider'] = provider_2fa

        if type(interactive) == bool:
            conf['interactive'] = interactive

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

        if isinstance(persisted_keys, (list, tuple)):
            conf['persistedKeys'] = persisted_keys
        elif not isinstance(conf.get('persistedKeys'), (list, tuple)):
            raise TypeError('persistedKeys conf item not of type {list, tuple}')

        # keep token logic for last, as renewal will possibly persist token+other data
        if token and type(token) == str:
            conf['token'] = token
        elif not conf.get('token') or conf.get('expiry', 0) - time.time() <= 5:
            # print('renewing token from Revolut init()')
            renew_token(conf)

        # self.logger.debug('effective config: {}'.format(conf))
        self.client = Client(conf=conf, token=True)


    def get_account_balances(self):
        """ Get the account balance for each currency
        and returns it as a dict {"balance":XXXX, "currency":XXXX} """
        ret = self.client._get(_URL_GET_ACCOUNTS)
        raw_accounts = ret.json()

        if 'pockets' not in raw_accounts:
            raise ApiChangedException('[pockets] key not in wallet response')

        account_balances = []
        for raw_account in raw_accounts.get("pockets"):
            account_balances.append({
                "balance": raw_account.get("balance"),
                "currency": raw_account.get("currency"),
                "type": raw_account.get("type"),
                "state": raw_account.get("state"),
                # name is present when the account is a vault (type = SAVINGS)
                "vault_name": raw_account.get("name", ""),
            })
        self.account_balances = Accounts(account_balances)
        return self.account_balances

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
            raise ConnectionError("Transaction error : %s" % ret.text)

        return exchange_transaction


class Account:
    """ Class to handle an account.
        Also known as a 'Wallet' on Revolut side.
    """
    def __init__(self, account_type, balance, state, vault_name):
        self.account_type = account_type  # CURRENT, SAVINGS
        self.balance = balance
        self.state = state  # ACTIVE, INACTIVE
        self.vault_name = vault_name
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
    """ Class to handle the account balances.
        Note Account is also known as Wallet in Revolut parlance.
    """

    def __init__(self, account_balances):
        self.raw_list = account_balances
        self.list = [
            Account(
                account_type=account.get("type"),
                balance=Amount(
                    currency=account.get("currency"),
                    revolut_amount=account.get("balance"),
                ),
                state=account.get("state"),
                vault_name=account.get("vault_name"),
            )
            for account in account_balances
        ]

    def get_account_by_name(self, account_name):
        """ Get an account by its name """
        for account in self.list:
            if account.name == account_name:
                return account

    def __len__(self):
        return len(self.list)

    def __getitem__(self, key):
        """ Method to access the object as a list
        (ex : accounts[1]) """
        return self.list[key]

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
            transactions_type,
            state,
            started_date,
            completed_date,
            amount,
            fee,
            description,
            account_id
        ):
        self.transactions_type = transactions_type
        self.state = state
        self.started_date = started_date
        self.completed_date = completed_date
        self.amount = amount
        self.fee = fee
        self.description = description
        self.account_id = account_id

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


class AccountTransactions:
    """ Class to handle the account transactions """

    def __init__(self, account_transactions):
        self.raw_list = account_transactions  # as received from Revolut api
        self.list = [
            AccountTransaction(
                transactions_type=transaction.get("type"),
                state=transaction.get("state"),
                started_date=transaction.get("startedDate"),
                completed_date=transaction.get("completedDate"),
                amount=Amount(revolut_amount=transaction.get('amount'),
                              currency=transaction.get('currency')),
                fee=transaction.get('fee'),
                description=transaction.get('description'),
                account_id=transaction.get('account').get('id')
            )
            for transaction in self.raw_list
        ]

    def __len__(self):
        return len(self.list)

    def csv(self, lang="en", reverse=False):
        lang_is_fr = lang == "fr"
        if lang_is_fr:
            csv_str = "Date-heure (DD/MM/YYYY HH:MM:ss);Description;Montant;Devise"
            date_format = "%d/%m/%Y %H:%M:%S"
        else:
            csv_str = "Date-time (MM/DD/YYYY HH:MM:ss),Description,Amount,Currency"
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
                    account_transaction.amount.currency
                ))
        return csv_str.replace(".", ",") if lang_is_fr else csv_str

from token_renewal import renew_token

