import threading
import asyncio
import time
import json
import subprocess
import colorama
import os

from utils import BitcoinRpc
from electrumutils import ElectrumX, ElectrumNode

from electrum import constants
constants.set_regtest()
from electrum.bitcoin import script_to_scripthash
from electrum.transaction import TxOutput
from electrum.bitcoin import TYPE_SCRIPT
from electrum.util import bh2u
from electrum.crypto import hash_160

x = ElectrumX()
x.start("doggman","donkey",18554)

elec = ElectrumNode(None,None,None,None)
elec.daemon.start()
time.sleep(1)
print(elec.info())

class bitcoind:
    rpc = BitcoinRpc(rpcport=18554, rpcuser='doggman', rpcpassword='donkey')
if bitcoind.rpc.getblockchaininfo()['blocks'] < 150:
    bitcoind.rpc.generate(150)
elec.addfunds(bitcoind, 100000000)

script = "0014" + bh2u(os.urandom(20))
scr = script_to_scripthash(script)
print("SCRIPTHASH WE ARE TESTING FOR", scr)

import queue

q= queue.Queue()

def req(proc):
    while None is proc.poll():
        line = proc.stdout.readline()
        if line:
            q.put(line)

    print("PROCESS DIED")

def generate_block(num=1):
    with subprocess.Popen(f'bitcoin-cli generate {num}'.split(' '), stdout=subprocess.PIPE) as btc:
        txids = json.loads(btc.stdout.read().strip())
    return txids

def make_tx(outputs, config):
    coins = elec.wallet.get_spendable_coins(None, config, nonlocal_only=True)
    from random import choice
    coins = [choice(coins)]
    tx = elec.wallet.make_unsigned_transaction(coins, outputs, config, None, None)
    elec.wallet.sign_transaction(tx, None)
    return tx

with subprocess.Popen(['nc', '-vvvvv', '127.0.0.1', '51001'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=1, universal_newlines=True) as proc:
    obj1 = {"id": 1, "method": "server.version", "params": ["testing", "1.4"]}
    proc.stdin.write(json.dumps(obj1) + "\n")
    print('version response', proc.stdout.readline())
    obj2 = {"id": 2, "method": "blockchain.scripthash.subscribe", "params": [scr]}
    for_server = f'{json.dumps(obj2)}\n'
    proc.stdin.write(for_server)
    t = threading.Thread(target=lambda: req(proc))
    t.start()
    i=3
    while True:
        print(colorama.Fore.RED + f"NEW RUN, NOW MINING {(i+1)**4} BLOCKS TO TRIGGER THE RACE" + colorama.Fore.RESET)

        coro = asyncio.run_coroutine_threadsafe(elec.wallet.network.get_history_for_scripthash(scr), elec.wallet.network.asyncio_loop)
        beforelen = len(coro.result(5))

        outputs = [TxOutput(TYPE_SCRIPT, script, 100000+i)]
        i+=1
        tx = make_tx(outputs,elec.wallet.network.config)
        coro = asyncio.run_coroutine_threadsafe(elec.wallet.network.broadcast_transaction(tx), elec.wallet.network.asyncio_loop)
        try:
            coro.result(1)
        except:
            generate_block()
            time.sleep(5)
            continue
        block_hash = generate_block(i**4)[0]
        assert bitcoind.rpc.getblock(block_hash)['tx'][1] == tx.txid()
        saw_yet = False
        while not saw_yet:
            # if this continues for too long, it is a bug in electrumx
            print("electrumx did not see ", tx.txid(), "yet")
            print("it is in block", block_hash,"but")
            print("try yourself:")
            print('printf \'{"id": 1, "method": "server.version", "params": ["testing", "1.4"]}\\n{"id": 2, "method": "blockchain.scripthash.get_history", "params":["' + scr + '"]}\\n\' | nc 127.0.0.1 51001')
            time.sleep(5)
            saw_yet = not q.empty()

        print('saw tx')
        while not q.empty():
            res= q.get()
            print('scripthash update response' , res)
            parsed = json.loads(res)
            if 'result' not in parsed or parsed['result'] is None:
                print("ignoring none result")
                continue
            coro = asyncio.run_coroutine_threadsafe(elec.wallet.network.get_history_for_scripthash(scr), elec.wallet.network.asyncio_loop)
            hist = coro.result(5)
            print("history", hist)
            assert len(hist) != beforelen
        time.sleep(5)

x.kill()

