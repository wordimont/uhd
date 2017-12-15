#
# Copyright 2017 Ettus Research, National Instruments Company
#
# SPDX-License-Identifier: GPL-3.0
#
"""
UDP Transport manager
"""

from builtins import object
from six import iteritems, itervalues
from usrp_mpm.ethtable import EthDispatcherTable
from usrp_mpm.sys_utils import net
from usrp_mpm.mpmtypes import SID
from usrp_mpm import lib

class XportMgrUDP(object):
    """
    Transport manager for UDP connections
    """
    # The interface configuration describes how the Ethernet interfaces are
    # hooked up to the crossbar and the FPGA. It could look like this:
    # iface_config = {
    #     'eth1': { # Add key for every Ethernet iface connected to the FPGA
    #         'label': 'misc-enet-regs0', # UIO label for the Eth table
    #         'xbar': 0, # Which crossbar? 0 -> /dev/crossbar0
    #         'xbar_port': 0, # Which port on the crossbar it is connected to
    #     },
    # }
    iface_config = {}
    # The control addresses are typically addresses bound to the controlling
    # UHD session. When the requested source address is below or equal to this
    # number, we override requested SID source addresses based on other logic.
    max_ctrl_addr = 1

    def __init__(self, log):
        assert len(self.iface_config)
        assert all((
            all((key in x for key in ('label', 'xbar', 'xbar_port')))
            for x in itervalues(self.iface_config)
        ))
        self.log = log
        self.log.trace("Initializing UDP xport manager...")
        self._possible_chdr_ifaces = self.iface_config.keys()
        self.log.trace("Identifying available network interfaces...")
        self.chdr_port = EthDispatcherTable.DEFAULT_VITA_PORT[0]
        self._chdr_ifaces = \
            self._init_interfaces(self._possible_chdr_ifaces)
        self._eth_dispatchers = {}
        self._allocations = {}

    def _init_interfaces(self, possible_ifaces):
        """
        Enumerate all network interfaces that are currently able to stream CHDR
        Returns a dictionary iface name -> iface info, where iface info is the
        return value of get_iface_info().
        """
        self.log.trace("Testing available interfaces out of `{}'".format(
            list(possible_ifaces)
        ))
        valid_ifaces = net.get_valid_interfaces(possible_ifaces)
        if len(valid_ifaces):
            self.log.debug("Found CHDR interfaces: `{}'".format(valid_ifaces))
        else:
            self.log.warning("No CHDR interfaces found!")
        return {
            x: net.get_iface_info(x)
            for x in valid_ifaces
        }

    def _update_dispatchers(self):
        """
        Updates the self._eth_dispatchers dictionary, makes sure that all IP
        addresses are programmed correctly.

        After calling this, _chdr_ifaces and _eth_dispatchers are in sync.
        """
        ifaces_to_remove = [
            x for x in self._eth_dispatchers.keys()
            if x not in self._chdr_ifaces
        ]
        for iface in ifaces_to_remove:
            self._eth_dispatchers.pop(iface)
        for iface in self._chdr_ifaces:
            if iface not in self._eth_dispatchers:
                self._eth_dispatchers[iface] = \
                    EthDispatcherTable(self.iface_config[iface]['label'])
            self._eth_dispatchers[iface].set_ipv4_addr(
                self._chdr_ifaces[iface]['ip_addr']
            )

    def init(self, args):
        """
        Call this when the user calls 'init' on the periph manager
        """
        self._chdr_ifaces = \
            self._init_interfaces(self._possible_chdr_ifaces)
        self._update_dispatchers()
        for _, table in iteritems(self._eth_dispatchers):
            if 'forward_eth' in args or 'forward_bcast' in args:
                table.set_forward_policy(
                    args.get('forward_eth', False),
                    args.get('forward_bcast', False)
                )
        if 'preload_ethtables' in args:
            self._preload_ethtables(
                self._eth_dispatchers,
                args['preload_ethtables']
            )

    def deinit(self):
        " Clean up after a session terminates "
        self._allocations = {}

    def _preload_ethtables(self, eth_dispatchers, table_file):
        """
        Populates the ethernet tables from a JSON file
        """
        import json
        try:
            eth_table_data = json.load(open(table_file))
        except ValueError as ex:
            self.log.warning(
                "Bad values in preloading table file: %s",
                str(ex)
            )
            return
        self.log.info(
            "Preloading Ethernet dispatch tables from JSON file `%s'.",
            table_file
        )
        for eth_iface, data in iteritems(eth_table_data):
            if eth_iface not in eth_dispatchers:
                self.log.warning(
                    "Request to preload eth dispatcher table for "
                    "iface `{}', but no such interface is "
                    "registered. Known interfaces: {}".format(
                        str(eth_iface),
                        ",".join(eth_dispatchers.keys())
                    )
                )
                continue
            eth_dispatcher = eth_dispatchers[eth_iface]
            self.log.debug("Preloading {} dispatch table".format(eth_iface))
            try:
                for dst_ep, udp_data in iteritems(data):
                    sid = SID()
                    sid.set_dst_ep(int(dst_ep))
                    eth_dispatcher.set_route(
                        sid,
                        udp_data['ip_addr'],
                        udp_data['port'],
                        udp_data.get('mac_addr', None)
                    )
            except ValueError as ex:
                self.log.warning(
                    "Bad values in preloading table file: %s",
                    str(ex)
                )

    def get_xbar_dev(self, iface):
        """
        Given an Ethernet interface (e.g., 'eth1') returns the crossbar device
        it is connected to.
        """
        xbar_idx = self.iface_config[iface]['xbar']
        return "/dev/crossbar{}".format(xbar_idx)

    def request_xport(
            self,
            sid,
            xport_type,
        ):
        """
        Return UDP xport info
        """
        def fixup_sid(sid, iface_name):
            " Modify the source SID (e.g. the UHD SID) "
            if sid.src_addr <= self.max_ctrl_addr:
                sid.src_addr = self.iface_config[iface_name]['ctrl_src_addr']
            return sid
        assert xport_type in ('CTRL', 'ASYNC_MSG', 'TX_DATA', 'RX_DATA')
        allocation_getter = lambda iface: {
            'CTRL': 0,
            'ASYNC_MSG': 0,
            'RX_DATA': self._allocations.get(iface, {}).get('rx', 0),
            'TX_DATA': self._allocations.get(iface, {}).get('tx', 0),
        }[xport_type]
        xport_info = [
            {
                'type': 'UDP',
                'ipv4': str(iface_info['ip_addr']),
                'port': str(self.chdr_port),
                'send_sid': str(fixup_sid(sid, iface_name)),
                'allocation': str(allocation_getter(iface_name)),
                'xport_type': xport_type,
            }
            for iface_name, iface_info in iteritems(self._chdr_ifaces)
        ]
        return xport_info

    def commit_xport(self, sid, xport_info):
        """
        fuu
        """
        self.log.trace("Sanity checking xport_info %s...", str(xport_info))
        assert xport_info['type'] == 'UDP'
        assert any([xport_info['ipv4'] == x['ip_addr']
                    for x in itervalues(self._chdr_ifaces)])
        assert xport_info['port'] == str(self.chdr_port)
        assert len(xport_info.get('src_ipv4')) > 5
        assert int(xport_info.get('src_port')) > 0
        sender_addr = xport_info['src_ipv4']
        sender_port = int(xport_info['src_port'])
        self.log.trace("Incoming connection is coming from %s:%d",
                       sender_addr, sender_port)
        mac_addr = net.get_mac_addr(sender_addr)
        if mac_addr is None:
            raise RuntimeError(
                "Could not find MAC address for IP address {}".format(
                    sender_addr))
        self.log.trace("Incoming connection is coming from %s",
                       mac_addr)
        eth_iface = net.ip_addr_to_iface(xport_info['ipv4'], self._chdr_ifaces)
        xbar_port = self.iface_config[eth_iface]['xbar_port']
        self.log.trace("Using Ethernet interface %s, crossbar port %d",
                       eth_iface, xbar_port)
        xbar_iface = lib.xbar.xbar.make(self.get_xbar_dev(eth_iface))
        xbar_iface.set_route(sid.src_addr, xbar_port)
        self._eth_dispatchers[eth_iface].set_route(
            sid.reversed(), sender_addr, sender_port)
        self.log.trace("UDP transport successfully committed!")
        if xport_info.get('xport_type') == 'TX_DATA':
            self._allocations[eth_iface]['tx'] = \
                self._allocations.get(eth_iface, {}).get('tx', 0) + 1
        if xport_info.get('xport_type') == 'RX_DATA':
            self._allocations[eth_iface]['rx'] = \
                self._allocations.get(eth_iface, {}).get('rx', 0) + 1
        self.log.trace(
            "New link allocations for %s: TX: %d  RX: %d",
            eth_iface,
            self._allocations.get(eth_iface, {}).get('tx', 0),
            self._allocations.get(eth_iface, {}).get('rx', 0),
        )
        return True

