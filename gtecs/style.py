"""Text colour and style functions."""

from . import params


def rtxt(text):
    """Print red coloured text."""
    if params.FANCY_OUTPUT:
        return '\033[31;1m' + str(text) + '\033[0m'
    else:
        return text


def gtxt(text):
    """Print green coloured text."""
    if params.FANCY_OUTPUT:
        return '\033[32;1m' + str(text) + '\033[0m'
    else:
        return text


def ytxt(text):
    """Print yellow coloured text."""
    if params.FANCY_OUTPUT:
        return '\033[33;1m' + str(text) + '\033[0m'
    else:
        return text


def btxt(text):
    """Print blue coloured text."""
    if params.FANCY_OUTPUT:
        return '\033[34;1m' + str(text) + '\033[0m'
    else:
        return text


def ptxt(text):
    """Print purple coloured text."""
    if params.FANCY_OUTPUT:
        return '\033[35;1m' + str(text) + '\033[0m'
    else:
        return text


def boldtxt(text):
    """Print bold text."""
    if params.FANCY_OUTPUT:
        return '\033[1m' + str(text) + '\033[0m'
    else:
        return text


def undltxt(text):
    """Print underlined text."""
    if params.FANCY_OUTPUT:
        return '\033[4m' + str(text) + '\033[0m'
    else:
        return text


def errortxt(message):
    """Print text prepended with a bold red ERROR."""
    return rtxt(boldtxt('ERROR')) + ': ' + str(message)
