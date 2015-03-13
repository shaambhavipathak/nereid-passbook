# -*- coding: utf-8 -*-
"""
    __init__.py

    :copyright: (c) 2014 by Openlabs Technologies & Consulting (P) Limited
    :license: BSD, see LICENSE for more details.
"""
from trytond.pool import Pool
from passbook import Pass, Registration


def register():
    Pool.register(
        Pass,
        Registration,
        module='nereid_passbook', type_='model'
    )
