import logging
import networkx
from .base_graph import BaseGraph
from .constants import NodeStatus


class WoTGraph(BaseGraph):
    def __init__(self, app, community, nx_graph=None):
        """
        Init WoTGraph instance
        :param sakia.core.app.Application app: Application instance
        :param sakia.core.community.Community community: Community instance
        :param networkx.Graph nx_graph: The networkx graph
        :return:
        """
        super().__init__(app, community, nx_graph)

    async def get_shortest_path_to_identity(self, account_identity, to_identity):
        """
        Return path list of nodes from from_identity to to_identity
        :param identity from_identity:
        :param identity to_identity:
        :return:
        """
        path = list()

        logging.debug("path between %s to %s..." % (account_identity.uid, to_identity.uid))
        self.add_identity(account_identity, NodeStatus.HIGHLIGHTED)

        # recursively feed graph searching for account node...
        await self.explore_to_find_member(account_identity, to_identity)

        # calculate path of nodes between identity and to_identity
        try:
            path = networkx.shortest_path(self.nx_graph, account_identity.pubkey, to_identity.pubkey)
        except networkx.NetworkXNoPath as e:
            logging.debug(str(e))

        return path

    async def explore_to_find_member(self, account_identity, to_identity):
        """
        Scan graph to find identity
        :param sakia.core.registry.Identity from_identity: Scan starting point
        :param sakia.core.registry.Identity to_identity: Scan goal
        """
        explored = []
        explorable = [account_identity]

        while len(explorable) > 0:
            current = explorable.pop()
            certifier_list = await current.unique_valid_certifiers_of(self.app.identities_registry,
                                                                                     self.community)
            await self.add_certifier_list(certifier_list, current, account_identity)
            if to_identity.pubkey in [data['identity'].pubkey for data in certifier_list]:
                return True

            certified_list = await current.unique_valid_certified_by(self.app.identities_registry,
                                                                                    self.community)
            await self.add_certified_list(certified_list, current, account_identity)
            if to_identity.pubkey in [data['identity'].pubkey for data in certified_list]:
                return True

            explored.append(current)
            for entry in certifier_list + certified_list:
                if entry['identity'] not in explored + explorable:
                    explorable.append(entry['identity'])
        return False