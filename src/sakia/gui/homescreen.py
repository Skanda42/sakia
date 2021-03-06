"""
Created on 31 janv. 2015

@author: vit
"""

from PyQt5.QtWidgets import QWidget, QFrame, QGridLayout, QAction
from PyQt5.QtCore import QEvent, Qt, pyqtSlot, pyqtSignal
from ..gen_resources.homescreen_uic import Ui_HomescreenWidget
from .community_tile import CommunityTile
from ..core.community import Community
import logging


class FrameCommunities(QFrame):
    community_tile_clicked = pyqtSignal(Community)

    def __init__(self, parent):
        super().__init__(parent)
        self.grid_layout = QGridLayout()
        self.setLayout(self.grid_layout)
        self.grid_layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setFrameShape(QFrame.StyledPanel)
        self.setFrameShadow(QFrame.Raised)
        self.tiles = []

    def sizeHint(self):
        return self.parentWidget().size()

    def refresh(self, app):
        for t in self.tiles:
            t.cancel_refresh()
            t.setParent(None)
        self.tiles = []
        if app.current_account:
            for c in app.current_account.communities:
                community_tile = CommunityTile(self, app, c)
                community_tile.clicked.connect(self.click_on_tile)
                self.layout().addWidget(community_tile)
                self.tiles.append(community_tile)

    def refresh_content(self):
        for t in self.tiles:
            t.refresh()

    @pyqtSlot()
    def click_on_tile(self):
        tile = self.sender()
        logging.debug("Click on tile")
        self.community_tile_clicked.emit(tile.community)


class HomeScreenWidget(QWidget, Ui_HomescreenWidget):
    """
    classdocs
    """

    def __init__(self, app, status_label):
        """
        Constructor
        """
        super().__init__()
        self.setupUi(self)
        self.app = app
        self.frame_communities = FrameCommunities(self)
        self.layout().addWidget(self.frame_communities)
        self.status_label = status_label

    def refresh(self):
        self.frame_communities.refresh(self.app)
        if self.app.current_account:
            self.frame_connected.show()
            self.label_connected.setText(self.tr("Connected as {0}".format(self.app.current_account.name)))
            self.frame_disconnected.hide()
        else:
            self.frame_disconnected.show()
            self.frame_connected.hide()

    def referential_changed(self):
        self.frame_communities.refresh_content()

    def showEvent(self, QShowEvent):
        """

        :param QShowEvent:
        :return:
        """
        self.frame_communities.refresh_content()
        self.status_label.setText("")

    def changeEvent(self, event):
        """
        Intercepte LanguageChange event to translate UI
        :param QEvent QEvent: Event
        :return:
        """
        if event.type() == QEvent.LanguageChange:
            self.retranslateUi(self)
        return super(HomeScreenWidget, self).changeEvent(event)


