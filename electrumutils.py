import logging
import binascii
from utils import BITCOIND_CONFIG
import time
import sys
import os
import tempfile
import traceback
import asyncio
from electrumx.server.controller import Controller
from electrumx.server.env import Env
import threading
from electrum.daemon import Daemon, get_fd_or_server
from electrum import simple_config, constants, keystore, util
from electrum.wallet import Wallet
from electrum.storage import WalletStorage
from electrum.mnemonic import Mnemonic

bh2u = lambda x: binascii.hexlify(x).decode('ascii')

class ElectrumX:
    def __init__(self):
        print("server/__init__")
        self.loop = asyncio.new_event_loop()
        self.evt = asyncio.Event(loop=self.loop)

    def kill(self):
        print("server/kill")
        async def set_evt():
            self.evt.set()
        asyncio.run_coroutine_threadsafe(set_evt(), self.loop)

    def start(self):
        print("server/start")
        from os import environ
        rpc_port = 8000
        os.system("rm -rf COIN hist meta utxo")
        environ.update({
            "COIN": "BitcoinSegwit",
            "SSL_KEYFILE": "/home/janus/electrumx/key.pem",
            "SSL_CERTFILE": "/home/janus/electrumx/cert.pem",
            "TCP_PORT": "51001",
            "SSL_PORT": "51002",
            "RPC_PORT": str(8000),
            "NET": "regtest",
            "DAEMON_URL": "http://rpcuser:rpcpass@127.0.0.1:" + str(BITCOIND_CONFIG.get('rpcport', 18332)),
            "DB_DIRECTORY": "."
        })
        def target():
            loop = self.loop
            asyncio.set_event_loop(loop)
            env = Env()
            env.loop_policy = asyncio.get_event_loop_policy()
            self.controller = Controller(env)
            logging.basicConfig(level=logging.DEBUG)
            loop.run_until_complete(asyncio.wait([self.controller.serve(self.evt), self.evt.wait()], return_when=asyncio.FIRST_COMPLETED))
            loop.close()

        self.thread = threading.Thread(target=target)
        self.thread.start()
        #controller.run()

    def wait(self):
        print("server/wait")
        self.thread.join()

class ElectrumDaemon:
    def __init__(self, electrumx):
        self.electrumx = electrumx
        self.port = -1
        self.actual = None

    def start(self):
        print("daemon/start")
        user_config = {
            "auto_connect": False,
            "oneserver": True,
            "server": "localhost:51001:t",
            "request_initial_sync": False,
        }
        user_dir = tempfile.mkdtemp(prefix="lightning-integration-electrum-")
        wallet_file_handle, wallet_file = tempfile.mkstemp(prefix="lightning-integration-electrum-wallet-")
        os.close(wallet_file_handle)
        os.unlink(wallet_file)
        #util.set_verbosity(False)
        constants.set_regtest()
        config = simple_config.SimpleConfig(user_config, None, lambda: user_dir)
        self.actual = Daemon(config, get_fd_or_server(config)[0])
        assert self.actual.network.asyncio_loop.is_running()
        storage = WalletStorage(wallet_file)
        k = keystore.from_seed(Mnemonic('en').make_seed('segwit'), "", False)
        storage.put('keystore', k.dump())
        storage.put('wallet_type', 'standard')
        storage.put('use_encryption', False)
        storage.write()
        wallet = Wallet(storage)
        wallet.synchronize() # otherwise lnwatcher will be initialized with None sweep_address
        wallet.start_network(self.actual.network)
        self.actual.add_wallet(wallet)
        self.actual.start()

    def stop(self):
        if not self.actual:
            return
        next(iter(self.actual.wallets.values())).stop_threads()
        self.actual.stop()

class ElectrumNode:
    displayName = "electrum"

    def __init__(self, lightning_dir, lightning_port, btc, electrumx, executor=None, node_id=0):
        print("node/__init__")
        self.daemon = ElectrumDaemon(electrumx)

    @property
    def wallet(self):
        wallets = list(self.daemon.actual.wallets.values())
        assert len(wallets) == 1
        return wallets[0]

    def peers(self):
        print("node/peers")
        return [bh2u(x) for x in self.wallet.lnworker.peers.keys()]

    def id(self):
        return bh2u(self.wallet.lnworker.pubkey)

    def openchannel(self, node_id, host, port, satoshis):
        print("node/openchannel")
        # second parameter is local_amt_sat not capacity!
        coro = self.wallet.lnworker.open_channel(node_id, satoshis, 0, pw=None)
        print("open channel result", coro.result())

    def addfunds(self, bitcoind, satoshis):
        print("node/addfunds")
        addr = self.wallet.get_unused_address()
        assert addr
        matured, unconfirmed, unmatured = self.wallet.get_addr_balance(addr)
        assert matured + unconfirmed + unmatured == 0
        assert addr is not None
        bitcoind.rpc.sendtoaddress(addr, float(satoshis) / 10**8)
        bitcoind.rpc.generate(1)
        while True:
            matured, unconfirmed, unmatured = self.wallet.get_addr_balance(addr)
            if matured + unmatured != 0:
                break
            time.sleep(1)
        print("funds added!")

    def ping(self):
        print("node/ping")
        return True

    def check_channel(self, remote):
        print("node/check_channel")
        try:
            chan = next(iter(self.wallet.lnworker.channels.values()))
        except StopIteration:
            return False
        else:
            return chan.get_state() == "OPEN"

    def getchannels(self):
        print("node/getchannels")
        channel_infos = self.wallet.network.path_finder.channel_db._id_to_channel_info.values()
        channels = set()
        for chan_info in channel_infos:
            channels.add((bh2u(chan_info.node_id_1), bh2u(chan_info.node_id_2)))
            channels.add((bh2u(chan_info.node_id_2), bh2u(chan_info.node_id_1)))
        return channels

    def getnodes(self):
        """ set of graph pubkeys excluding self """
        print("node/getnodes")
        channel_infos = self.wallet.network.path_finder.channel_db._id_to_channel_info.values()
        nodes = set()
        for chan_info in channel_infos:
            nodes.add(bh2u(chan_info.node_id_1))
            nodes.add(bh2u(chan_info.node_id_2))
        nodes.remove(self.wallet.lnworker.pubkey)
        return nodes

    def invoice(self, amount):
        print("node/invoice")
        return self.wallet.lnworker.add_invoice(amount, "cup of coffee")

    def send(self, req):
        print("node/send")
        addr, peer, coro = self.wallet.lnworker.pay(req)
        coro.result(5)
        coro = peer.payment_preimages[addr.paymenthash].get()
        preimage = asyncio.run_coroutine_threadsafe(coro, self.wallet.network.asyncio_loop).result(5)
        return bh2u(preimage)

    def connect(self, host, port, node_id):
        self.wallet.lnworker.add_peer(host, port, bytes.fromhex(node_id))

    def info(self):
        local_height = self.daemon.actual.network.get_local_height()
        print("node/info: height: {}".format(local_height))
        return {'blockheight': local_height, 'id': self.id()}

    def block_sync(self, blockhash):
        print("node/block_sync")
        time.sleep(1)

    def restart(self):
        print("node/restart")
        self.daemon.stop()
        time.sleep(5)
        self.daemon = ElectrumDaemon(electrumx)
        self.daemon.start()
