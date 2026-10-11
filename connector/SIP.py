from gdo.base.Logger import Logger
from gdo.base.Message import Message
from gdo.base.Render import Mode
from gdo.core.Connector import Connector
from gdo.core.GDO_Server import GDO_Server
from gdo.sip.connector.ChatStream import ChatStream


class SIP(Connector):

    def get_render_mode(self) -> Mode:
        return Mode.render_txt

    def render_user_connect_help(self) -> str:
        return 'Local SIP bridge'

    def gdo_needs_authentication(self) -> bool:
        return False

    @classmethod
    def get_server(cls) -> GDO_Server:
        return GDO_Server.get_by_connector('sip')

    async def gdo_connect(self) -> bool:
        self._connected = True
        return True

    def chatstream_event(self, stream: ChatStream) -> str:
        return stream.to_event()

    async def gdo_send_to_channel(self, msg: Message):
        Logger.message('SIP reply dropped: no text-to-speech bridge is configured.')

    async def gdo_send_to_user(self, msg: Message, notice: bool = False):
        Logger.message('SIP reply dropped: no text-to-speech bridge is configured.')
