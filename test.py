from camoufox.sync_api import Camoufox

with Camoufox(
        os="macos",
        geoip=True,
        proxy={
            'server': 'http://31.58.229.203:2270',
            'username': 'user293669',
            'password': 'abjeyr'
        }
    ) as browser:
    page = browser.new_page()
    page.goto("https://polymarket.com")