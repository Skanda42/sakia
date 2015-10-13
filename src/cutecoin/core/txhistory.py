import asyncio
import logging
import hashlib
from .transfer import Transfer, TransferState
from ucoinpy.documents.transaction import InputSource, OutputSource
from ucoinpy.documents.block import Block
from ..tools.exceptions import LookupFailureError, NoPeerAvailable
from ucoinpy.api import  bma


class TxHistory():
    def __init__(self, app, wallet):
        self._latest_block = 0
        self.wallet = wallet
        self.app = app
        self._stop_coroutines = False
        self._running_refresh = []
        self._transfers = []
        self.available_sources = []
        self._dividends = []

    @property
    def latest_block(self):
        return self._latest_block

    @latest_block.setter
    def latest_block(self, value):
        self._latest_block = value

    def load_from_json(self, data):
        self._transfers = []

        data_sent = data['transfers']
        for s in data_sent:
            self._transfers.append(Transfer.load(s))

        for s in data['sources']:
            self.available_sources.append(InputSource.from_inline(s['inline']))

        for d in data['dividends']:
            d['state'] = TransferState[d['state']]
            self._dividends.append(d)

        self.latest_block = data['latest_block']

    def jsonify(self):
        data_transfer = []
        for s in self.transfers:
            data_transfer.append(s.jsonify())

        data_sources = []
        for s in self.available_sources:
            s.index = 0
            data_sources.append({'inline': "{0}\n".format(s.inline())})

        data_dividends = []
        for d in self._dividends.copy():
            d['state'] = d['state'].name
            data_dividends.append(d)

        return {'latest_block': self.latest_block,
                'transfers': data_transfer,
                'sources': data_sources,
                'dividends': data_dividends}

    @property
    def transfers(self):
        return [t for t in self._transfers if t.state != TransferState.DROPPED]

    @property
    def dividends(self):
        return self._dividends.copy()

    def stop_coroutines(self):
        self._stop_coroutines = True

    def _get_block_doc(self, community, number):
        """
        Retrieve the current block document
        :param cutecoin.core.Community community: The community we look for a block
        :param int number: The block number to retrieve
        :return: the block doc or None if no block was found
        """
        tries = 0
        block_doc = None
        block = None
        while block is None and tries < 3:
            try:
                block = yield from community.bma_access.future_request(bma.blockchain.Block,
                                      req_args={'number': number})
                signed_raw = "{0}{1}\n".format(block['raw'],
                                           block['signature'])
                try:
                    block_doc = Block.from_signed_raw(signed_raw)
                except TypeError:
                    logging.debug("Error in {0}".format(number))
                    block = None
                    tries += 1
            except ValueError as e:
                if '404' in str(e):
                    block = None
                    tries += 1
        return block_doc

    @asyncio.coroutine
    def _parse_transaction(self, community, tx, blockid,
                           mediantime, received_list, txid):
        """
        Parse a transaction
        :param cutecoin.core.Community community: The community
        :param ucoinpy.documents.Transaction tx: The tx json data
        :param ucoinpy.documents.BlockId blockid: The block id where we found the tx
        :param int mediantime: Median time on the network
        :param list received_list: The list of received transactions
        :param int txid: The latest txid
        :return: the found transaction
        """
        receivers = [o.pubkey for o in tx.outputs
                     if o.pubkey != tx.issuers[0]]

        if len(receivers) == 0:
            receivers = [tx.issuers[0]]

        try:
            issuer = yield from self.wallet._identities_registry.future_find(tx.issuers[0], community)
            issuer_uid = issuer.uid
        except LookupFailureError:
            issuer_uid = ""

        try:
            receiver = yield from self.wallet._identities_registry.future_find(receivers[0], community)
            receiver_uid = receiver.uid
        except LookupFailureError:
            receiver_uid = ""

        metadata = {
                    'time': mediantime,
                    'comment': tx.comment,
                    'issuer': tx.issuers[0],
                    'issuer_uid': issuer_uid,
                    'receiver': receivers[0],
                    'receiver_uid': receiver_uid,
                    'txid': txid
                    }

        in_issuers = len([i for i in tx.issuers
                     if i == self.wallet.pubkey]) > 0
        in_outputs = len([o for o in tx.outputs
                       if o.pubkey == self.wallet.pubkey]) > 0

        # We check if the transaction correspond to one we sent
        # but not from this cutecoin Instance
        tx_hash = hashlib.sha1(tx.signed_raw().encode("ascii")).hexdigest().upper()
        # If the wallet pubkey is in the issuers we sent this transaction
        if in_issuers:
            outputs = [o for o in tx.outputs
                       if o.pubkey != self.wallet.pubkey]
            amount = 0
            for o in outputs:
                amount += o.amount
            metadata['amount'] = amount
            transfer = Transfer.create_from_blockchain(tx_hash,
                                                       blockid,
                                                 metadata.copy())
            return transfer
        # If we are not in the issuers,
        # maybe it we are in the recipients of this transaction
        elif in_outputs:
            outputs = [o for o in tx.outputs
                       if o.pubkey == self.wallet.pubkey]
            amount = 0
            for o in outputs:
                amount += o.amount
            metadata['amount'] = amount

            transfer = Transfer.create_from_blockchain(tx_hash,
                                                       blockid,
                                                 metadata.copy())
            received_list.append(transfer)
            return transfer
        return None

    @asyncio.coroutine
    def _parse_block(self, community, block_number, received_list, txmax):
        """
        Parse a block
        :param cutecoin.core.Community community: The community
        :param int block_number: The block to request
        :param list received_list: The list where we are appending transactions
        :param int txmax: Latest tx id
        :return: The list of transfers sent
        """
        block_doc = yield from self._get_block_doc(community, block_number)
        transfers = []
        if block_doc:
            for transfer in [t for t in self._transfers if t.state == TransferState.AWAITING]:
                transfer.run_state_transitions((False, block_doc))

            new_tx = [t for t in block_doc.transactions
                      if t.sha_hash not in [trans.sha_hash for trans in self._transfers]
                      ]

            for (txid, tx) in enumerate(new_tx):
                transfer = yield from self._parse_transaction(community, tx, block_doc.blockid,
                                        block_doc.mediantime, received_list, txid+txmax)
                if transfer != None:
                    #logging.debug("Transfer amount : {0}".format(transfer.metadata['amount']))
                    transfers.append(transfer)
                else:
                    pass
                    #logging.debug("None transfer")
        else:
            logging.debug("Could not find or parse block {0}".format(block_number))
        return transfers

    @asyncio.coroutine
    def request_dividends(self, community, parsed_block):
        for i in range(0, 6):
            try:
                dividends_data = yield from community.bma_access.future_request(bma.ud.History,
                                                req_args={'pubkey': self.wallet.pubkey})

                dividends = dividends_data['history']['history'].copy()

                for d in dividends:
                    if d['block_number'] < parsed_block:
                        dividends.remove(d)
                return dividends
            except ValueError as e:
                if '404' in str(e):
                    pass
        return {}

    @asyncio.coroutine
    def _refresh(self, community, block_number_from, block_to, received_list):
        """
        Refresh last transactions

        :param cutecoin.core.Community community: The community
        :param list received_list: List of transactions received
        """
        new_transfers = []
        new_dividends = []
        try:
            logging.debug("Refresh from : {0} to {1}".format(block_number_from, block_to['number']))
            dividends = yield from self.request_dividends(community, block_number_from)
            with_tx_data = yield from community.bma_access.future_request(bma.blockchain.TX)
            members_pubkeys = yield from community.members_pubkeys()
            fork_window = community.network.fork_window(members_pubkeys)
            blocks_with_tx = with_tx_data['result']['blocks']
            while block_number_from <= block_to['number']:
                udid = 0
                for d in [ud for ud in dividends if ud['block_number'] == block_number_from]:
                    state = TransferState.VALIDATED if block_number_from + fork_window <= block_to['number'] \
                        else TransferState.VALIDATING

                    if d['block_number'] not in [ud['block_number'] for ud in self._dividends]:
                        d['id'] = udid
                        d['state'] = state
                        new_dividends.append(d)

                        udid += 1
                    else:
                        known_dividend = [ud for ud in self._dividends
                                          if ud['block_number'] == d['block_number']][0]
                        known_dividend['state'] = state

                # We parse only blocks with transactions
                if block_number_from in blocks_with_tx:
                    transfers = yield from self._parse_block(community, block_number_from,
                                                             received_list,
                                                             udid + len(new_transfers))
                    new_transfers += transfers

                self.wallet.refresh_progressed.emit(block_number_from, block_to['number'], self.wallet.pubkey)
                block_number_from += 1

            signed_raw = "{0}{1}\n".format(block_to['raw'],
                                       block_to['signature'])
            block_to = Block.from_signed_raw(signed_raw)
            for transfer in [t for t in self._transfers + new_transfers if t.state == TransferState.VALIDATING]:
                transfer.run_state_transitions((False, block_to, fork_window))

            # We check if latest parsed block_number is a new high number
            if block_number_from > self.latest_block:
                self.available_sources = yield from self.wallet.sources(community)
                if self._stop_coroutines:
                    return
                self.latest_block = block_number_from

            parameters = yield from community.parameters()
            for transfer in [t for t in self._transfers if t.state == TransferState.AWAITING]:
                transfer.run_state_transitions((False, block_to,
                                                parameters['avgGenTime'], parameters['medianTimeBlocks']))
        except NoPeerAvailable as e:
            logging.debug(str(e))
            self.wallet.refresh_finished.emit([])
            return

        self._transfers = self._transfers + new_transfers
        self._dividends = self._dividends + new_dividends

        self.wallet.refresh_finished.emit(received_list)

    @asyncio.coroutine
    def _check_block(self, community, block_number):
        """
        Parse a block
        :param cutecoin.core.Community community: The community
        :param int block_number: The block to check for transfers
        """
        block_doc = yield from self._get_block_doc(community, block_number)

        # We check if transactions are still present
        for transfer in [t for t in self._transfers
                         if t.state in (TransferState.VALIDATING, TransferState.VALIDATED) and
                         t.blockid.number == block_number]:
            return not transfer.run_state_transitions((True, block_doc))
        else:
            return False

    @asyncio.coroutine
    def _rollback(self, community):
        """
        Rollback last transactions until we find one still present
        in the main blockchain

        :param cutecoin.core.Community community: The community
        """
        try:
            logging.debug("Rollback from : {0}".format(self.latest_block))
            # We look for the block goal to check for rollback,
            #  depending on validating and validated transfers...
            tx_blocks = [tx.blockid.number for tx in self._transfers
                          if tx.state in (TransferState.VALIDATED, TransferState.VALIDATING) and
                          tx.blockid is not None]
            tx_blocks.reverse()
            for i, block_number in enumerate(tx_blocks):
                self.wallet.refresh_progressed.emit(i, len(tx_blocks), self.wallet.pubkey)
                if (yield from self._check_block(community, block_number)):
                    break

            current_block = yield from self._get_block_doc(community, community.network.current_blockid.number)
            members_pubkeys = yield from community.members_pubkeys()
            fork_window = community.network.fork_window(members_pubkeys)
            # We check if transactions VALIDATED are in the fork window now
            for transfer in [t for t in self._transfers
                             if t.state == TransferState.VALIDATED]:
                transfer.run_state_transitions((True, current_block, fork_window))
        except NoPeerAvailable:
            logging.debug("No peer available")

    @asyncio.coroutine
    def refresh(self, community, received_list):
        # We update the block goal
        try:
            current_block_number = community.network.current_blockid.number
            if current_block_number:
                current_block = yield from community.bma_access.future_request(bma.blockchain.Block,
                                        req_args={'number': current_block_number})
                members_pubkeys = yield from community.members_pubkeys()
                # We look for the first block to parse, depending on awaiting and validating transfers and ud...
                tx_blocks = [tx.blockid.number for tx in self._transfers
                          if tx.state in (TransferState.AWAITING, TransferState.VALIDATING) \
                         and tx.blockid is not None]
                ud_blocks = [ud['block_number'] for ud in self._dividends
                          if ud['state'] in (TransferState.AWAITING, TransferState.VALIDATING)]
                blocks = tx_blocks + ud_blocks + \
                         [max(0, self.latest_block - community.network.fork_window(members_pubkeys))]
                block_from = min(set(blocks))

                yield from self._wait_for_previous_refresh()
                if block_from < current_block["number"]:
                    # Then we start a new one
                    logging.debug("Starts a new refresh")
                    task = asyncio.async(self._refresh(community, block_from, current_block, received_list))
                    self._running_refresh.append(task)
        except ValueError as e:
            logging.debug("Block not found")
        except NoPeerAvailable:
            logging.debug("No peer available")

    @asyncio.coroutine
    def rollback(self, community, received_list):
        yield from self._wait_for_previous_refresh()
        # Then we start a new one
        logging.debug("Starts a new rollback")
        task = asyncio.async(self._rollback(community))
        self._running_refresh.append(task)

        # Then we start a refresh to check for new transactions
        yield from self.refresh(community, received_list)

    @asyncio.coroutine
    def _wait_for_previous_refresh(self):
        # We wait for current refresh coroutines
        if len(self._running_refresh) > 0:
            logging.debug("Wait for the end of previous refresh")
            done, pending = yield from asyncio.wait(self._running_refresh)
            for cor in done:
                try:
                    self._running_refresh.remove(cor)
                except ValueError:
                    logging.debug("Task already removed.")
            for p in pending:
                logging.debug("Still waiting for : {0}".format(p))
            logging.debug("Previous refresh finished")
        else:
            logging.debug("No previous refresh")
