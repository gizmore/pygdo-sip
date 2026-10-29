from gdo.base.GDO_Module import GDO_Module
from gdo.base.GDT import GDT
from gdo.core.Connector import Connector
from gdo.core.GDO_Server import GDO_Server
from gdo.sip.connector.SIP import SIP
from gdo.sip.GDT_Phone import GDT_Phone


class module_sip(GDO_Module):

    def gdo_module_config(self) -> list[GDT]:
        return [
        ]

    def gdo_user_settings(self) -> list[GDT]:
        return [
            GDT_Phone(),
        ]

    def gdo_dependencies(self) -> list:
        return ['account', 'audio', 'mira']

    def gdo_init(self):
        Connector.register(SIP, False)

    async def gdo_install(self):
        if not GDO_Server.get_by_connector('sip'):
            GDO_Server.blank({
                'serv_name': 'SIP',
                'serv_connector': 'sip',
                'serv_trigger': '$',
            }).insert()
