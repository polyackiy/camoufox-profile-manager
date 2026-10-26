"""Protocol guidance and literal credential handling in both user-facing forms."""

import unittest

from playwright.sync_api import expect
from stage_one_ui import DesktopUI


class ProxyProtocolUI(DesktopUI):
    def test_library_blocks_unsupported_socks_auth_before_a_write_and_preserves_literal_credentials(self):
        self.open_proxy_library()
        self.page.get_by_role('button',name='New proxy',exact=True).click()
        dialog=self.page.get_by_role('dialog',name='New library proxy')
        dialog.get_by_label('Name',exact=True).fill('Literal proxy')
        dialog.get_by_label('Server',exact=True).fill('proxy.example:1080')
        dialog.get_by_label('Type',exact=True).select_option('socks4')
        dialog.get_by_label('Username',exact=True).fill(' user ')
        dialog.get_by_label('Password (optional)',exact=True).fill(' password ')
        expect(dialog.get_by_role('alert')).to_contain_text('SOCKS4 credentials are not supported')
        expect(dialog.get_by_role('button',name='Create proxy',exact=True)).to_be_disabled()
        dialog.get_by_label('Type',exact=True).select_option('socks5')
        dialog.get_by_role('button',name='Create proxy',exact=True).click()
        expect(dialog).to_have_count(0)
        import json
        writes=[json.loads(body) for method,path,body in self.calls if method=='POST' and path=='/proxies']
        self.assertEqual(len(writes),1)
        self.assertEqual((writes[0]['username'],writes[0]['password']),(' user ',' password '))

    def test_profile_preserves_literal_socks_credentials_and_rejects_byte_overflow(self):
        self.page.goto(self.origin+'/')
        self.page.get_by_role('button',name='New profile',exact=True).click()
        dialog=self.page.get_by_role('dialog',name='New profile')
        dialog.get_by_label('Name',exact=True).fill('Literal profile')
        dialog.get_by_label('Type',exact=True).select_option('socks5')
        dialog.get_by_label('Server',exact=True).fill('proxy.example:1080')
        dialog.get_by_label('Username',exact=True).fill('界'*86)
        dialog.get_by_label('Password',exact=True).fill(' password ')
        expect(dialog.get_by_role('alert')).to_contain_text('1–255 UTF-8 bytes')
        expect(dialog.get_by_role('button',name='Create profile',exact=True)).to_be_disabled()
        dialog.get_by_label('Username',exact=True).fill(' user ')
        dialog.get_by_role('button',name='Create profile',exact=True).click()
        expect(dialog).to_have_count(0)
        import json
        writes=[json.loads(body) for method,path,body in self.calls if method=='POST' and path=='/profiles']
        self.assertEqual((writes[0]['proxy_config']['username'],writes[0]['proxy_config']['password']),(' user ',' password '))

    def test_chinese_socks4_guidance_explains_remote_dns_and_limits(self):
        self.page.set_viewport_size({'width':375,'height':812})
        self.open_proxy_library()
        self.page.get_by_label('Language',exact=True).select_option('zh-CN')
        self.page.get_by_role('button',name='新建代理',exact=True).click()
        self.page.get_by_label('类型',exact=True).select_option('socks4')
        expect(self.page.get_by_text('SOCKS4 使用远程 DNS（SOCKS4a）。不支持用户名/密码认证和 IPv6 目标地址；需要这些功能时请使用 SOCKS5。',exact=True)).to_be_visible()
        dialog=self.page.get_by_role('dialog',name='新建代理')
        self.assertLessEqual(dialog.evaluate('(element) => element.scrollWidth'),dialog.evaluate('(element) => element.clientWidth'))


if __name__=='__main__':
    suite=unittest.TestSuite(ProxyProtocolUI(name) for name in ProxyProtocolUI.__dict__ if name.startswith('test_'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
