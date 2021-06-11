#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import click
import sys

from revolut import Revolut, __version__

# Usage : revolut_cli.py --help

@click.command()
@click.option(
    '--device-id', '-d',
    envvar="REVOLUT_DEVICE_ID",
    type=str,
    help='your Revolut token (or set the env var REVOLUT_DEVICE_ID)',
)
@click.option(
    '--token', '-t',
    envvar="REVOLUT_TOKEN",
    type=str,
    help='your Revolut token (or set the env var REVOLUT_TOKEN)',
)
@click.option(
    '--password', '-p',
    envvar="REVOLUT_PASSWORD",
    type=str,
    help='your Revolut pin/password (or set the env var REVOLUT_PASSWORD)',
)
@click.option(
    '--phone', '-P',
    envvar="REVOLUT_PHONE",
    type=str,
    help='your Revolut phone number (or set the env var REVOLUT_PHONE)',
)
@click.option(
    '--language', '-l',
    type=str,
    help='language ("fr" or "en"), for the csv header and separator',
    default='en'
)
@click.option(
    '--account', '-a',
    type=str,
    help='account name (ex : "EUR CURRENT") to get the balance for the account'
 )
@click.option(
    '--channel', '-c',
    type=click.Choice(['EMAIL', 'SMS', 'APP']),
    help='auth channel to use',
 )
@click.version_option(
    version=__version__,
    message='%(prog)s, based on [revolut] package version %(version)s'
)
def main(device_id, token, password, phone, language, account, channel):
    """ Get the account balances on Revolut """

    rev = Revolut(device_id=device_id, token=token, password=password, phone=phone, channel=channel, interactive=True)
    account_balances = rev.get_account_balances()
    if account:
        a = account_balances.get_account_by_name(account)
        if a:
            print(a.balance)
        else:
            print('no account for [{}] found'.format(account))
    else:
        print(account_balances.csv(lang=language))


if __name__ == "__main__":
    main()
