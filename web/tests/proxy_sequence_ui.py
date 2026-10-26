"""Ordered proxy selection and explicit confirmation, including uncertain responses."""

import copy
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import expect
from stage_two_ui import StageTwoUI


class ProxySequenceUI(StageTwoUI):
    def setUp(self):
        super().setUp()
        self.library.append({**self.library[0], 'id': 'library-two', 'name': 'Second', 'server': 'second.example:1080', 'type': 'socks5'})
        self.previews = []
        self.assignments = []
        self.drop_once = False
        self.partial = False
        self.running_state_failure = False
        self.legacy_password = 'private-legacy-password'

    def route(self, route):
        path = urlsplit(route.request.url).path.split('/api/v1', 1)[1]
        if path == '/browsers/active' and self.running_state_failure:
            return route.fulfill(status=503, json={'error': {'message': 'unavailable'}})
        if path == '/proxies/assignment-preview':
            body = route.request.post_data_json
            self.previews.append(copy.deepcopy(body))
            items = []
            for pair in body['assignments']:
                profile = next(item for item in self.profiles if item['id'] == pair['profile']['id'])
                proxy = next(item for item in self.library if item['id'] == pair['proxy']['id'])
                items.append({'profile_id': profile['id'], 'profile_name': profile['name'], 'proxy_id': proxy['id'], 'proxy_name': proxy['name'], 'connection': f"{proxy['type']}://{proxy['server']}", 'current_connection': 'http://old.example:8080' if profile['proxy_config'] else 'Direct connection', 'replaces_proxy': bool(profile['proxy_config']), 'reason': None})
            return route.fulfill(json={'success': True, 'data': {'items': items}})
        if path == '/proxies/assign-sequence':
            body = route.request.post_data_json
            self.assignments.append(copy.deepcopy(body))
            if self.drop_once:
                self.drop_once = False
                return route.abort('failed')
            ids = [pair['profile']['id'] for pair in body['assignments']]
            return route.fulfill(json={'success': True, 'data': {'assigned': ids[:-1] if self.partial else ids, 'skipped': [{'id': ids[-1], 'reason': f'provider failure {self.legacy_password}'}] if self.partial else []}})
        super().route(route)

    def open_dialog(self):
        self.page.goto(self.origin + '/proxies/')
        self.page.get_by_role('button', name='Assign proxy list', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='Assign proxy list')
        expect(dialog.get_by_role('checkbox', name='Select Account 001', exact=True)).to_be_visible()
        return dialog

    def select_order(self, dialog):
        dialog.get_by_role('checkbox', name='Select Account 002', exact=True).check()
        dialog.get_by_role('checkbox', name='Select Account 001', exact=True).check()
        dialog.get_by_role('checkbox', name='Select proxy Second', exact=True).check()
        dialog.get_by_role('checkbox', name='Select proxy Office', exact=True).check()

    def test_exact_order_is_reviewed_before_any_assignment(self):
        dialog = self.open_dialog()
        self.select_order(dialog)
        dialog.get_by_role('button', name='Preview mapping', exact=True).click()
        items = dialog.get_by_role('list', name='Proxy mapping preview').get_by_role('listitem')
        expect(items.nth(0)).to_contain_text('Account 002')
        expect(items.nth(0)).to_contain_text('Second')
        expect(items.nth(1)).to_contain_text('Office')
        self.assertEqual(self.assignments, [])
        dialog.get_by_role('button', name='Confirm assignment', exact=True).click()
        expect(dialog.get_by_text('2 assigned; 0 skipped', exact=True)).to_be_visible()
        self.assertEqual(self.previews[0], self.assignments[0])
        self.assertEqual([pair['profile']['id'] for pair in self.assignments[0]['assignments']], ['profile-2', 'profile-1'])
        self.assertNotIn('password', str(self.assignments))

    def test_insufficient_proxy_pool_stays_editable_without_preview_request(self):
        dialog = self.open_dialog()
        self.select_order(dialog)
        dialog.get_by_role('checkbox', name='Select proxy Office', exact=True).uncheck()
        dialog.get_by_role('button', name='Preview mapping', exact=True).click()
        expect(dialog.get_by_role('alert')).to_contain_text('Select the same number')
        self.assertEqual(self.previews, [])
        dialog.get_by_role('checkbox', name='Select proxy Office', exact=True).check()
        dialog.get_by_role('button', name='Preview mapping', exact=True).click()
        expect(dialog.get_by_role('list', name='Proxy mapping preview')).to_be_visible()

    def test_existing_proxy_replacement_requires_confirmation_and_hides_userinfo(self):
        self.profiles[0]['proxy_config'] = {'type': 'http', 'server': f'http://old-user:{self.legacy_password}@old.example:8080'}
        dialog = self.open_dialog()
        self.select_order(dialog)
        dialog.get_by_role('button', name='Preview mapping', exact=True).click()
        expect(dialog.get_by_role('button', name='Confirm assignment', exact=True)).to_be_disabled()
        self.assertNotIn(self.legacy_password, dialog.inner_text())
        dialog.get_by_role('checkbox', name='Replace existing proxy settings for the selected profile.', exact=True).check()
        dialog.get_by_role('button', name='Confirm assignment', exact=True).click()
        expect(dialog.get_by_text('2 assigned; 0 skipped', exact=True)).to_be_visible()

    def test_response_loss_retains_original_pairs_and_versions_without_automatic_retry(self):
        self.drop_once = True
        dialog = self.open_dialog()
        self.select_order(dialog)
        dialog.get_by_role('button', name='Preview mapping', exact=True).click()
        dialog.get_by_role('button', name='Confirm assignment', exact=True).click()
        expect(dialog.get_by_role('alert')).to_contain_text('No automatic retry')
        self.assertEqual(len(self.assignments), 1)
        dialog.get_by_role('button', name='Retry original mapping', exact=True).click()
        expect(dialog.get_by_text('2 assigned; 0 skipped', exact=True)).to_be_visible()
        self.assertEqual(self.assignments[0], self.assignments[1])

    def test_partial_result_is_explicit_and_unknown_error_is_redacted(self):
        self.partial = True
        dialog = self.open_dialog()
        self.select_order(dialog)
        dialog.get_by_role('button', name='Preview mapping', exact=True).click()
        dialog.get_by_role('button', name='Confirm assignment', exact=True).click()
        expect(dialog.get_by_text('1 assigned; 1 skipped', exact=True)).to_be_visible()
        expect(dialog.get_by_text('Assignment could not be confirmed. Reload profiles before trying again.', exact=True)).to_be_visible()
        self.assertNotIn(self.legacy_password, dialog.inner_text())
        expect(dialog.get_by_role('button', name='Retry original mapping', exact=True)).to_have_count(0)

    def test_search_keeps_hidden_selected_profiles_in_their_original_order(self):
        dialog = self.open_dialog()
        dialog.get_by_label('Search profiles', exact=True).fill('002')
        dialog.get_by_role('checkbox', name='Select Account 002', exact=True).check()
        dialog.get_by_label('Search profiles', exact=True).fill('001')
        dialog.get_by_role('checkbox', name='Select Account 001', exact=True).check()
        dialog.get_by_role('checkbox', name='Select proxy Office', exact=True).check()
        dialog.get_by_role('checkbox', name='Select proxy Second', exact=True).check()
        dialog.get_by_role('button', name='Preview mapping', exact=True).click()
        expect(dialog.get_by_role('list', name='Proxy mapping preview').get_by_role('listitem').nth(0)).to_contain_text('Account 002')
        self.assertEqual([item['profile']['id'] for item in self.previews[0]['assignments']], ['profile-2', 'profile-1'])

    def test_unavailable_running_state_blocks_the_new_assignment_flow(self):
        self.running_state_failure = True
        self.page.goto(self.origin + '/proxies/')
        self.page.get_by_role('button', name='Assign proxy list', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='Assign proxy list')
        expect(dialog.get_by_role('alert')).to_contain_text('Could not load profiles, proxies and running state')
        expect(dialog.get_by_role('button', name='Preview mapping', exact=True)).to_be_disabled()
        self.assertEqual(self.previews, [])

    def test_chinese_mapping_in_compact_window_with_keyboard_controls(self):
        self.page.add_init_script("localStorage.setItem('camoufox-pm.ui-language', 'zh-CN')")
        self.page.set_viewport_size({'width': 375, 'height': 812})
        self.page.goto(self.origin + '/proxies/')
        self.page.get_by_role('button', name='按列表分配代理', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='按列表分配代理')
        profile = dialog.get_by_role('checkbox', name='选择 Account 001', exact=True)
        profile.focus()
        profile.press('Space')
        dialog.get_by_role('checkbox', name='选择代理 Office', exact=True).check()
        dialog.get_by_role('button', name='预览分配关系', exact=True).click()
        expect(dialog.get_by_role('list', name='代理分配关系预览')).to_contain_text('Account 001')
        dialog.get_by_role('button', name='确认分配', exact=True).click()
        expect(dialog.get_by_text('已分配 1 个，已跳过 0 个', exact=True)).to_be_visible()
        self.assertLessEqual(dialog.evaluate('(element) => element.scrollWidth'), dialog.evaluate('(element) => element.clientWidth'))
        self.page.keyboard.press('Escape')
        expect(dialog).to_have_count(0)


if __name__ == '__main__':
    suite = unittest.TestSuite(ProxySequenceUI(name) for name in ProxySequenceUI.__dict__ if name.startswith('test_'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
