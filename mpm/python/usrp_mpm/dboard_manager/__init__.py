#
# Copyright 2017-2018 Ettus Research, a National Instruments Company
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""
dboards module __init__.py
"""
from .base import DboardManagerBase
from usrp_mpm import __simulated__
if not __simulated__:
    from .magnesium import Magnesium
    from .rhodium import Rhodium
    from .neon import Neon
    from .e31x_db import E31x_db
    from .eiscat import EISCAT
    from .empty_slot import EmptySlot
    from .zbx import ZBX
    from .test import test
    from .unknown import unknown
    from .dboard_iface import DboardIface
    from .x4xx_db_iface import TitaniumDboardIface
    from .titanium_debug_db import TitaniumDebugDboard
    from .titanium_if_test_cca import TitaniumIfTestCCA
