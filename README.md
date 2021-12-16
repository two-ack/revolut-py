# revolut-py

[![Travis](https://img.shields.io/travis/tducret/revolut-python.svg)](https://travis-ci.org/tducret/revolut-python)
[![Coveralls github](https://img.shields.io/coveralls/github/tducret/revolut-python.svg)](https://coveralls.io/github/tducret/revolut-python)
[![PyPI](https://img.shields.io/pypi/v/revolut.svg)](https://pypi.org/project/revolut/)
![License](https://img.shields.io/github/license/tducret/revolut-python.svg)

# Description

Unofficial client for the [Revolut Bank](https://www.revolut.com/). It uses the same
api as Revolut's own [webapp](https://app.revolut.com).


# Requirements

- Python 3.7+


# Installation

```bash
python3 -m pip install --user --upgrade revolut-py
```


# Configuration

revolut-py config dir is located under `$XDG_CONFIG_HOME/revolut-py`
(eg `~/.config/revolut-py`) by default, but can be customized via constructor: `Revolut(root_conf_dir=mydir)`

Config dir will have one or more json-based config files:
- `config` - stores the main/common configuration;
- `<phone_number>.config` - stores per-account configuration. Eg access token (and
its expiry) queried from Revolut will be stored here. It's not advisable
modifying this manually; better let revolut-py take care of it. If you don't want
any account-specific data persisted, set `persistedKeys` in common conf to empty list.

Example config @ `/home/myuser/.config/revolut-py/config`:

```json
{
    "channel": "SMS",
    "phone": "+33612345678",
    "selfie": "/home/myuser/.config/revolut-py/selfies/",
    "userAgent": "Mozilla/5.0 (X11; Linux x86_64; rv:87.0) Gecko/20100101 Firefox/87.0",
    "persistedKeys": ["pass", "token", "expiry", "device"]
}
```

Note creating a configuration file is not required, as all necessary configs can be passed
via `Revolut()` constructor (which also overrides ones defined in config file). It is
however convenient way of defining your own defaults.


## Configuration file options

| Config key | Description | Default | Example |
| --- | --- | --- | --- |
| channel       | Channel used for 2FA auth. One of {EMAIL,SMS,APP} | EMAIL | SMS |
| phone         | Phone number/account to use by default | None | +33612345678 |
| selfie        | Path to either a selfie file or directory containing multiple selfies. Needed for 3FA auth step | None | /home/myuser/.config/revolut-py/selfie.jpg |
| geo           | Coordinates to send via Revolut requests | None | 37.91490,-3.78380 |
| userAgent     | Browser user agent to send via Revolut requests | Changes often, see [code](https://github.com/laur89/revolut-py/blob/master/revolut/__init__.py#L89) | Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:15.0) Gecko/20100101 Firefox/15.0.1 |
| persistedKeys | List of configuration keys that should be stored under per-account config file | ["token", "expiry", "device"] | ["pass", "token", "expiry", "device"] |


## Constructor args

`Revolut` class constructor signature:

```python
class Revolut:
    def __init__(self, device_id=None, token=None,
                 password=None, phone=None, channel=None,
                 persisted_keys=None, provider_2fa=None,
                 interactive=None, root_conf_dir=None):
```

As mentioned previously, most of these are optional, as missing options are picked up 
from config file(s) or generated automatically. Couple of options deserve further explanation though:

| Key | Description | Default | Example |
| --- | --- | --- | --- |
| provider_2fa  | Callable accepting phone number as argument and returning 2FA code sent by Revolut | None | lambda phone: my_func_providing_2fa_code() |
| interactive   | Whether we're running in interactive mode. In interactive mode missing config items are asked from user | False | True |
| root_conf_dir | See description above under 'Configuration' | $XDG_CONFIG_HOME/revolut-py | /path/to/own/dir/ |


# Usage

This package is mainly intended to be used as a python library for automatic
pulling of account data & transactions. It does however include `revolut_cli.py`
& `revolut_transactions.py` CLI scripts as a quick showcase for using the lib, although
they are arguably useful on their own.


## Use as library

Quick example:

```python
#!/usr/bin/env python3

from revolut import Revolut

rev = Revolut(password=1234, phone='+33612345678', interactive=True)
print(rev.get_account_balances().csv())
```


## Use included scripts

As mentioned before, couple of scripts are included with the project, but are
mainly intended as a demonstration of the `Revolut` library.

### Show the account balances : revolut_cli.py

```bash
# for usage help, run
revolut_cli.py --help
 ```

 Example output:

 ```csv
Account name,Balance,Currency
EUR CURRENT,100.50,EUR
GBP CURRENT,20.00,GBP
USD CURRENT,0.00,USD
AUD CURRENT,0.00,AUD
BTC CURRENT,0.00123456,BTC
EUR SAVINGS (My vault),10.30,EUR
```


### Pulling transactions : revolut_transactions.py

```bash
# for usage help, run
revolut_transactions.py --help
 ```

 Example output:

 ```csv
Date-time,Description,Amount,Currency
08/26/2019 21:31:00,Card Delivery Fee,-59.99,SEK
09/14/2019 12:50:07,donkey.bike **pending**,0.0,SEK
09/14/2019 13:03:15,Top-Up by *6458,200.0,SEK
09/30/2019 16:19:19,Reward user for the invite,200.0,SEK
10/12/2019 23:51:02,Tiptapp Reservation,-250.0,SEK
```


## Credits

This project is forked from https://github.com/tducret/revolut-python


## TODO

- [ ] Document revolutbot.py
- [ ] Create a RaspberryPi Dockerfile for revolutbot (to check if rates grows very often)
- [ ] Improve coverage for revolutbot
