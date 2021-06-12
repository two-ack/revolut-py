# -*- coding: utf-8 -*-
"""
This package defines the logic for renewing our Revolut api token
"""

import base64
import string
import random
import os
import json
import time
from typing import Optional
from revolut import Client, API_BASE
from retry_decorator import retry


_URL_GET_TOKEN_STEP1 = API_BASE + "/signin"
_URL_GET_TOKEN_STEP2 = API_BASE + "/signin/confirm"
_URL_GET_TOKEN_STEP2_APP = API_BASE + '/token'
_URL_SELFIE = API_BASE + '/biometric-signin/selfie'
_URL_3FA_CONFIRM_TMPLT = API_BASE + '/biometric-signin/confirm/{}'
_3FAT = "thirdFactorAuthAccessToken"


# TODO: raise ConnectionError here instead?
def validate_token_response(response):
    for i in 'user', 'accessToken', 'tokenExpiryDate':
        if i not in response:
            raise RuntimeError('required key [{}] not in response payload'.format(i))
    if 'id' not in response['user']:
        raise RuntimeError('required key [user.id] not in response payload')


def get_token(conf) -> (str, float):

    tokenId = get_token_step1(conf)
    # print('!!!! got tokenid {}'.format(tokenId))

    response = get_token_step2(conf, tokenId)

    if _3FAT in response:
        userId = response['user']['id']
        access_token = response[_3FAT]
        response = signin_biometric(userId, access_token, conf)

    validate_token_response(response)
    token_expiry = int(response['tokenExpiryDate']) / 1000  # unix epoch, eg 1623064046.252
    token = extract_token(response)

    if conf.get('interactive'):
        token_str = '' if 'token' in conf.get('persistedKeys') else "Your token is {}".format(token)
        device_id_str = '' if 'device' in conf.get('persistedKeys') else "Your device id is {}".format(conf.get('device'))
        if token_str or device_id_str:
            dashes = len(token_str) * "-"
            print('')
            print(dashes)
            for i in token_str, device_id_str:
                if i: print(i)
            print(dashes)
            print('')

    return token, token_expiry


def token_encode(f, s) -> str:
    token_to_encode = "{}:{}".format(f, s).encode("ascii")
    # Ascii encoding required by b64encode function : 8 bits char as input
    token = base64.b64encode(token_to_encode).decode("ascii")

    # print('encoded TOKEN: {}'.format(token))
    return token


def is_valid_2fa_code(code) -> bool:
    if type(code) == str:
        return len(code) == 6 and all(d in string.digits for d in code)
    elif type(code) == int:
        return len(str(code)) == 6
    else:
        return False


def get_token_step1(conf, simulate=False) -> Optional[str]:
    """ Function to obtain a Revolut token if APP channel is used.
        No payload received if channel != APP
    """
    if simulate:  # TODO: these are all wrong after change
        return "SMS"

    c = Client(conf=conf, renew_token=False)
    channel = conf.get('channel')
    data = {"phone": conf.get('phone'), "password": conf.get('pass'), "channel": channel}
    ret = c._post(_URL_GET_TOKEN_STEP1, json=data)

    if channel == 'APP':
        return ret.json().get("tokenId")
    elif channel not in ['EMAIL', 'SMS']:
        raise NotImplementedError('channel [{}] support not implemented'.format(channel))
    # no token is received if channel = {EMAIL,SMS}


def get_token_step2(conf, token, simulate=False) -> json:
    """ Function to obtain a Revolut token (step 2 : with code) """
    if simulate:
        # Because we don't want to receive a code through sms
        # for every test ;)
        simu = '{"user":{"id":"fakeuserid","createdDate":123456789,\
        "address":{"city":"my_city","country":"FR","postcode":"12345",\
        "region":"my_region","streetLine1":"1 rue mon adresse",\
        "streetLine2":"Appt 1"},\"birthDate":[1980,1,1],"firstName":"John",\
        "lastName":"Doe","phone":"+33612345678","email":"myemail@email.com",\
        "emailVerified":false,"state":"ACTIVE","referralCode":"refcode",\
        "kyc":"PASSED","termsVersion":"2018-05-25","underReview":false,\
        "riskAssessed":false,"locale":"en-GB"},"wallet":{"id":"wallet_id",\
        "ref":"12345678","state":"ACTIVE","baseCurrency":"EUR",\
        "topupLimit":3000000,"totalTopup":0,"topupResetDate":123456789,\
        "pockets":[{"id":"pocket_id","type":"CURRENT","state":"ACTIVE",\
        "currency":"EUR","balance":100,"blockedAmount":0,"closed":false,\
        "creditLimit":0}]},"accessToken":"myaccesstoken"}'
        raw_get_token = json.loads(simu)
    else:

        c = Client(conf=conf, renew_token=False)
        channel = conf.get('channel')
        provider_2fa = conf.get('2FAProvider')

        if channel in ['EMAIL', 'SMS']:
            if provider_2fa:
                code = str(provider_2fa()).replace("-", "").strip()
                if not is_valid_2fa_code(code):
                    raise ValueError('incorrect 2FA code provided by the automation: [{}]'.format(code))
            elif not conf.get('interactive'):
                raise RuntimeError('no 2FA code nor code provider defined')
            else:
                while True:
                    code = input(
                        "Please enter the 6-digit code you received by {} "
                        "[ex : 123456] : ".format(channel)
                    ).replace("-", "").strip()

                    if is_valid_2fa_code(code):
                        break
                    print('verification code should consist of 6 digits, but [{}] was provided'.format(code))

            # TODO: unsure why, but on web first req is sent w/o password, followed by same payload w/ passwd added
            data = {"phone": conf.get('phone'), "code": code}
            res = c._post(_URL_GET_TOKEN_STEP2, expected_status_code=204, json=data)

            data.update({"password": conf.get('pass')})
            res = c._post(_URL_GET_TOKEN_STEP2, expected_status_code=204, json=data)

            data.update({"limitedAccess": False})
            res = c._post(_URL_GET_TOKEN_STEP2, json=data)
            res = res.json()
        elif channel == 'APP':
            print('please authorize signin via phone app...')

            data = {"phone": conf.get('phone'), "password": conf.get('pass'), "tokenId": token}
            count = 0
            while True:
                if count > 50:
                    raise RuntimeError('waited for [{}] iterations for successful auth from mobile app (2FA)'.format(count))
                time.sleep(conf.get('app2FASleepLoopSec', 3))
                ret = c._post(_URL_GET_TOKEN_STEP2_APP, expected_status_code=[200, 422], json=data)
                res = ret.json()
                if ret.status_code == 200: break
#             "text": "{\"message\":\"One should obtain consent from the user before continuing\",\"code\":9035}" response while waiting for app-based accepting
                if 'code' not in res or res['code'] != 9035:
                    raise ConnectionError(
                        'sent error code for [{}] was unexpected: {}'.format(
                            _URL_GET_TOKEN_STEP2_APP, res['code']))
                count += 1

        raw_get_token = res

    return raw_get_token



def extract_token(json_response) -> str:
    user_id = json_response["user"]["id"]
    access_token = json_response["accessToken"]
    return token_encode(user_id, access_token)


def listdir_fullpath(d) -> [str]:
    return [os.path.join(d, f) for f in os.listdir(d)]


# 3FA logic
def signin_biometric(userId, access_token, conf) -> json:
    dir_or_file = conf.get('selfie')
    if dir_or_file and os.path.isfile(dir_or_file):
        selfie_filepath = dir_or_file
    elif dir_or_file and os.path.isdir(dir_or_file) and os.scandir(dir_or_file):  # is valid, non-empty dir
        selfie_filepath = random.choice(listdir_fullpath(dir_or_file))  # select random file from directory
    elif not conf.get('interactive'):
        raise RuntimeError('no selfie file or dir location defined')
    else:
        print()
        print("Selfie 3rd factor authentication was requested.")
        while True:
            selfie_filepath = input("Provide a selfie image file path (800x600) [ex : selfie.png] ")
            if os.path.isfile(selfie_filepath):
                break
            print('provided path [{}] does not correspond to valid file'.format(selfie_filepath))

    # sanity:
    # TODO: also confirm file is a valid jpg
    if not os.path.isfile(selfie_filepath):
        raise IOError('selected selfie file [{}] is not a valid file'.format(selfie_filepath))

    token = token_encode(userId, access_token)
    c = Client(conf=conf, token=token, renew_token=False)

    with open(selfie_filepath, 'rb') as f:
        # define 'files' so request's file-part headers would look like:
        # Content-Disposition: form-data; name="selfie"; filename="selfie.jpg"
        # Content-Type: image/jpeg
        files = {'selfie': ('selfie.jpg', f, 'image/jpeg')}
        res = c._post(_URL_SELFIE, files=files)

    biometric_id = res.json()["id"]
    url = _URL_3FA_CONFIRM_TMPLT.format(biometric_id)

    count = 0
    while True:
        if count > 50:
            raise RuntimeError('waited for [{}] iterations for successful biometric (3FA) confirmation response'.format(count))
        time.sleep(conf.get('3FASleepLoopSec', 2))
        res = c._post(url, expected_status_code=[200, 204])
        if res.status_code == 200: break
        count += 1

    return res.json()


@retry(ConnectionError, tries=2)
def renew_token(conf) -> str:
    # print(' !!! starting renew_token() flow...')
    t, e = get_token(conf)
    conf['token'] = t
    conf['expiry'] = e

    _write_conf(conf)
    return t


def _write_conf(conf) -> None:
    """Persist per-account config & token data"""

    if not conf or not conf.get('accConf'):
        return

    data = {}
    for i in conf.get('persistedKeys'):
        if i in conf:
            data[i] = conf.get(i)
    if not data: return

    try:
        with open(conf.get('accConf'), 'w') as f:
            f.write(
                json.dumps(
                    data,
                    indent=4,
                    sort_keys=True,
                    separators=(',', ': '),
                    ensure_ascii=False))
        # self.logger.debug('wrote conf: {}'.format(data))
    except IOError as e:
        raise e

