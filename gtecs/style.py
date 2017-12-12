"""
Text colour and style functions
"""

from . import params


def rtxt(text):
    if params.FANCY_OUTPUT:
        return '\033[31;1m' + str(text) + '\033[0m'
    else:
        return text


def gtxt(text):
    if params.FANCY_OUTPUT:
        return '\033[32;1m' + str(text) + '\033[0m'
    else:
        return text


def ytxt(text):
    if params.FANCY_OUTPUT:
        return '\033[33;1m' + str(text) + '\033[0m'
    else:
        return text


def btxt(text):
    if params.FANCY_OUTPUT:
        return '\033[34;1m' + str(text) + '\033[0m'
    else:
        return text


def ptxt(text):
    if params.FANCY_OUTPUT:
        return '\033[35;1m' + str(text) + '\033[0m'
    else:
        return text


def bold(text):
    if params.FANCY_OUTPUT:
        return '\033[1m' + str(text) + '\033[0m'
    else:
        return text


def undl(text):
    if params.FANCY_OUTPUT:
        return '\033[4m' + str(text) + '\033[0m'
    else:
        return text


def ERROR(message):
    return rtxt(bold('ERROR')) + ': ' + str(message)
