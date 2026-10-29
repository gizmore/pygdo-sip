from gdo.base.GDT import GDT
from gdo.base.Method import Method
from gdo.core.GDT_User import GDT_User
from gdo.sip.connector.SIP import SIP, phone_username


class call(Method):
    @classmethod
    def gdo_trigger(cls) -> str:
        return 'call'

    def gdo_parameters(self) -> list[GDT]:
        return [
            GDT_User('user').not_null(),
        ]

    async def gdo_execute(self) -> GDT:
        user = self.param_value('user')
        if phone_username(user.get_setting_val('phone')) is None:
            return self.err('err_hat_kein_phone', (user.render_name(),))
        connector = SIP.get_server().get_connector()
        try:
            await connector.gdo_call_user(user)
        except RuntimeError:
            return self.err('err_sip_call_unavailable')
        return self.reply('msg_sip_call_started', (user.render_name(),))
