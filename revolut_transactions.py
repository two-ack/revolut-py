#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import click
import json
import os

from datetime import datetime
from datetime import timedelta

from revolut import Revolut, __version__


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
    '--channel', '-c',
    type=click.Choice(['EMAIL', 'SMS', 'APP']),
    help='auth channel to use',
 )
@click.option(
    '--language', '-l',
    type=click.Choice(['en', 'fr']),
    help='language for the csv header and separator',
    default='en'
)
@click.option(
    '--from-date', '-F',
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help='transactions lookback date in YYYY-MM-DD format (ex: "2019-10-26"). Default 30 days back',
    default=(datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
)
@click.option(
    '--to-date', '-T',
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help='transactions lookforward date in YYYY-MM-DD format (ex: "2019-10-30")'
)
@click.option(
    '--output-format', '-f',
    type=click.Choice(['csv', 'json']),
    help="output format",
    default='csv',
)
@click.option(
    '--reverse', '-r',
    is_flag=True,
    help='reverse the order of the transactions displayed',
)
def main(device_id, token, password, phone, channel, language, from_date, to_date, output_format, reverse=False):
    """ Get the account balances on Revolut """
    rev = Revolut(device_id=device_id, token=token, password=password, phone=phone, channel=channel, interactive=True)
    account_transactions = rev.get_account_transactions(from_date, to_date)
    if output_format == 'csv':
        print(account_transactions.csv(lang=language, reverse=reverse))
    elif output_format == 'json':
        transactions = account_transactions.raw_list
        if reverse:
            transactions = reversed(transactions)
        d = json.dumps(
                transactions,
                indent=4,
                separators=(',', ': '),
                ensure_ascii=False)
        print(d)
    else:
        print("output format {!r} not implemented".format(output_format))
        exit(1)


if __name__ == "__main__":
    main()
