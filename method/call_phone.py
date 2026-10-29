from gdo.base.GDT import GDT
from gdo.base.Method import Method
from gdo.core.GDO_Permission import GDO_Permission
from gdo.sip.GDT_Phone import GDT_Phone
from gdo.sip.connector.SIP import SIP


class call_phone(Method):
    """Owner-only direct dialing for a supplied phone number."""

    @classmethod
    def gdo_trigger(cls) -> str:
        return 'call_phone'

    def gdo_user_permission(self) -> str | None:
        return GDO_Permission.OWNER

    def gdo_parameters(self) -> list[GDT]:
        return [GDT_Phone('phone').not_null()]

    async def gdo_execute(self) -> GDT:
        connector = SIP.get_server().get_connector()
        try:
            await connector.gdo_call_phone(self.param_value('phone'))
        except RuntimeError:
            return self.err('err_sip_call_unavailable')
        return self.reply('msg_sip_direct_call_started')
