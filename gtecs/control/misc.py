"""Miscellaneous common functions."""

from . import params


def valid_ints(array, allowed):
    """Return valid ints from a list."""
    valid = []
    for i in array:
        if i == '':
            pass
        elif (not i.isdigit()) or (i not in [str(x) for x in allowed]):
            print('"{}" is invalid, must be in {}'.format(i, allowed))
        elif int(i) not in valid:
            valid += [int(i)]
    valid.sort()
    return valid


def valid_strings(array, allowed):
    """Return valid strings from a list."""
    valid = []
    for i in array:
        if i == '':
            pass
        elif i not in [str(x) for x in allowed]:
            print('"{}" is invalid, must be in {}'.format(i, allowed))
        elif i not in valid:
            valid += [i]
    return valid


def is_num(value):
    """Return if a value is a valid number."""
    try:
        float(value)
        return True
    except ValueError:
        return False


def ut_list_to_mask(ut_list):
    """Convert a UT list to a mask integer."""
    ut_mask = 0
    for ut in params.UTS:
        if ut in ut_list:
            ut_mask += 2**(ut - 1)
    return ut_mask


def ut_mask_to_string(ut_mask):
    """Convert a UT mask integer to a string of 0s and 1s."""
    total_uts = max(params.UTS)
    bin_str = format(ut_mask, '0{}b'.format(total_uts))
    ut_str = bin_str[-1 * total_uts:]
    return ut_str


def ut_string_to_list(ut_string):
    """Convert a UT string of 0s and 1s to a list."""
    ut_list = []
    for ut in params.UTS:
        if ut_string[-1 * ut] == '1':
            ut_list.append(ut)
    ut_list.sort()
    return ut_list
