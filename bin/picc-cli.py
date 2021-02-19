import os
import argparse

def stop_all():
    os.system('sudo systemctl stop currentduino.service')
    os.system('sudo systemctl stop hemtduino.service')
    os.system('sudo systemctl stop quenchmon.service')
    os.system('sudo systemctl stop picc.service')
    os.system('sudo systemctl stop lakeshore240.service')
    os.system('sudo systemctl stop sim960.service')
    os.system('sudo systemctl stop sim921.service')

helpdesc=('Picture-C CLI')
VERSION=0.1

if __name__=='__main__':
    cli_parser = argparse.ArgumentParser(description=helpdesc, add_help=True)
    cli_parser.add_argument('--version', action='version', version=VERSION)
    cli_parser.add_argument('command',  dest='cmd', action='store', required=True, type=str,
                            help='Action to take')
    args = cli_parser.parse_args()

    if args.cmd=='stop':
        stop_all()
