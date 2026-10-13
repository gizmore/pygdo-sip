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
        return True

    def gdo_has_channels(self) -> bool:
        return True

    @classmethod
    def get_server(cls) -> GDO_Server:
        return GDO_Server.get_by_connector('sip')

    async def gdo_connect(self) -> bool:
        # Registration is not a transport. Do not report a nonexistent listener.
        self._connected = False
        return False

    def chatstream_event(self, stream: ChatStream) -> str:
        return stream.to_event()

    async def gdo_send_to_channel(self, msg: Message):
        raise RuntimeError('SIP send unavailable: no active call transport or speech output.')

    async def gdo_send_to_user(self, msg: Message, notice: bool = False):
        raise RuntimeError('SIP send unavailable: no verified active call mapping or transport.')
