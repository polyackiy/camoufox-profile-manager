#!/usr/bin/env python3
"""
Улучшенная утилита для проверки корректности миграции куков между Chrome и Camoufox
Учитывает различия в форматах доменов и другие нюансы
"""

import asyncio
import sqlite3
import json
import os
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime
import argparse

from .cookie_decryptor import ChromeCookieDecryptor
from loguru import logger


class AdvancedCookieMigrationChecker:
    """Улучшенный класс для проверки корректности миграции куков"""
    
    def __init__(self):
        self.chrome_decryptor = ChromeCookieDecryptor()
        
    def normalize_domain(self, domain: str) -> str:
        """Нормализовать домен для сравнения (убрать/добавить точку)"""
        if not domain:
            return ""
        
        # Убираем точку в начале для унификации
        if domain.startswith('.'):
            return domain[1:]
        return domain
    
    def get_chrome_cookies(self, chrome_profile_path: str) -> List[Dict[str, Any]]:
        """Получить куки из профиля Chrome"""
        logger.info(f"🔍 Читаем куки из Chrome профиля: {chrome_profile_path}")
        
        try:
            cookies = self.chrome_decryptor.get_decrypted_chrome_cookies(chrome_profile_path)
            
            if cookies:
                logger.success(f"✅ Извлечено {len(cookies)} куков из Chrome")
                return cookies
            else:
                logger.warning("⚠️ Куки не найдены или не удалось расшифровать")
                return []
                
        except Exception as e:
            logger.error(f"❌ Ошибка при извлечении куков из Chrome: {e}")
            return []
    
    def get_camoufox_cookies(self, profile_id: str) -> List[Dict[str, Any]]:
        """Получить куки из профиля Camoufox"""
        logger.info(f"🔍 Читаем куки из Camoufox профиля: {profile_id}")
        
        cookies_db_path = f"data/profiles/profile_{profile_id}/cookies.sqlite"
        if not os.path.exists(cookies_db_path):
            logger.error(f"❌ Файл куков Camoufox не найден: {cookies_db_path}")
            return []
        
        try:
            conn = sqlite3.connect(cookies_db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT name, value, host, path, expiry, isSecure, isHttpOnly, sameSite
                FROM moz_cookies
            """)
            
            cookies = []
            for row in cursor.fetchall():
                cookie = {
                    'name': row[0],
                    'value': row[1],
                    'domain': row[2],
                    'path': row[3],
                    'expires': row[4] if row[4] else None,
                    'secure': bool(row[5]),
                    'httponly': bool(row[6]),
                    'samesite': row[7] if row[7] else 'unspecified'
                }
                cookies.append(cookie)
            
            conn.close()
            logger.success(f"✅ Найдено {len(cookies)} куков в Camoufox")
            return cookies
            
        except Exception as e:
            logger.error(f"❌ Ошибка при чтении куков из Camoufox: {e}")
            return []
    
    def normalize_chrome_cookies(self, chrome_cookies: List[Dict]) -> List[Dict]:
        """Нормализовать структуру куков Chrome"""
        normalized = []
        for cookie in chrome_cookies:
            normalized_cookie = {
                'name': cookie.get('name', ''),
                'value': cookie.get('value', ''),
                'domain': self.normalize_domain(cookie.get('host_key', '')),
                'path': cookie.get('path', '/'),
                'expires': cookie.get('expires_utc'),
                'secure': cookie.get('is_secure', False),
                'httponly': cookie.get('is_httponly', False),
                'samesite': cookie.get('samesite', 0)
            }
            normalized.append(normalized_cookie)
        return normalized
    
    def normalize_camoufox_cookies(self, camoufox_cookies: List[Dict]) -> List[Dict]:
        """Нормализовать структуру куков Camoufox"""
        normalized = []
        for cookie in camoufox_cookies:
            normalized_cookie = {
                'name': cookie.get('name', ''),
                'value': cookie.get('value', ''),
                'domain': self.normalize_domain(cookie.get('domain', '')),
                'path': cookie.get('path', '/'),
                'expires': cookie.get('expires'),
                'secure': cookie.get('secure', False),
                'httponly': cookie.get('httponly', False),
                'samesite': cookie.get('samesite', 'unspecified')
            }
            normalized.append(normalized_cookie)
        return normalized
    
    def compare_cookies_advanced(self, chrome_cookies: List[Dict], camoufox_cookies: List[Dict]) -> Dict[str, Any]:
        """Продвинутое сравнение куков с учетом нормализации доменов"""
        logger.info("🔄 Выполняем продвинутое сравнение куков...")
        
        chrome_normalized = self.normalize_chrome_cookies(chrome_cookies)
        camoufox_normalized = self.normalize_camoufox_cookies(camoufox_cookies)
        
        # Создаем индексы для поиска
        chrome_index = {f"{c['domain']}:{c['name']}": c for c in chrome_normalized}
        camoufox_index = {f"{c['domain']}:{c['name']}": c for c in camoufox_normalized}
        
        results = {
            'total_chrome': len(chrome_normalized),
            'total_camoufox': len(camoufox_normalized),
            'exact_matches': 0,
            'value_matches': 0,
            'missing_in_camoufox': [],
            'extra_in_camoufox': [],
            'value_mismatches': [],
            'attribute_mismatches': [],
            'domain_format_differences': []
        }
        
        # Анализируем куки из Chrome
        for key, chrome_cookie in chrome_index.items():
            if key in camoufox_index:
                camoufox_cookie = camoufox_index[key]
                
                # Проверяем точное совпадение значений
                if chrome_cookie['value'] == camoufox_cookie['value']:
                    results['exact_matches'] += 1
                    results['value_matches'] += 1
                else:
                    # Проверяем частичное совпадение (возможно, разные кодировки)
                    if self.values_similar(chrome_cookie['value'], camoufox_cookie['value']):
                        results['value_matches'] += 1
                    else:
                        results['value_mismatches'].append({
                            'domain': chrome_cookie['domain'],
                            'name': chrome_cookie['name'],
                            'chrome_value_preview': self.preview_value(chrome_cookie['value']),
                            'camoufox_value_preview': self.preview_value(camoufox_cookie['value'])
                        })
                
                # Проверяем атрибуты
                attr_diffs = self.compare_cookie_attributes(chrome_cookie, camoufox_cookie)
                if attr_diffs:
                    results['attribute_mismatches'].append({
                        'domain': chrome_cookie['domain'],
                        'name': chrome_cookie['name'],
                        'differences': attr_diffs
                    })
            else:
                results['missing_in_camoufox'].append({
                    'domain': chrome_cookie['domain'],
                    'name': chrome_cookie['name'],
                    'value_preview': self.preview_value(chrome_cookie['value'])
                })
        
        # Проверяем лишние куки в Camoufox
        for key, camoufox_cookie in camoufox_index.items():
            if key not in chrome_index:
                results['extra_in_camoufox'].append({
                    'domain': camoufox_cookie['domain'],
                    'name': camoufox_cookie['name'],
                    'value_preview': self.preview_value(camoufox_cookie['value'])
                })
        
        return results
    
    def values_similar(self, value1: str, value2: str) -> bool:
        """Проверить, похожи ли значения куков (учитываем возможные различия в кодировке)"""
        if not value1 or not value2:
            return False
        
        # Простая проверка на частичное совпадение
        if len(value1) == len(value2):
            # Проверяем первые и последние символы
            if value1[:10] == value2[:10] and value1[-10:] == value2[-10:]:
                return True
        
        return False
    
    def preview_value(self, value: str, max_len: int = 30) -> str:
        """Создать превью значения куки для отображения"""
        if not value:
            return ""
        
        if len(value) <= max_len:
            return value
        
        return value[:max_len] + "..."
    
    def compare_cookie_attributes(self, chrome_cookie: Dict, camoufox_cookie: Dict) -> List[str]:
        """Сравнить атрибуты куков"""
        differences = []
        
        if chrome_cookie.get('secure', False) != camoufox_cookie.get('secure', False):
            differences.append('secure')
        
        if chrome_cookie.get('httponly', False) != camoufox_cookie.get('httponly', False):
            differences.append('httponly')
        
        if chrome_cookie.get('path', '/') != camoufox_cookie.get('path', '/'):
            differences.append('path')
        
        return differences
    
    def print_advanced_report(self, results: Dict[str, Any]):
        """Вывести продвинутый отчет о сравнении куков"""
        logger.info("📊 Продвинутый отчет о миграции куков:")
        logger.info(f"Chrome куков: {results['total_chrome']}")
        logger.info(f"Camoufox куков: {results['total_camoufox']}")
        logger.info(f"Точных совпадений: {results['exact_matches']}")
        logger.info(f"Совпадений по значению: {results['value_matches']}")
        
        # Вычисляем проценты
        if results['total_chrome'] > 0:
            exact_rate = (results['exact_matches'] / results['total_chrome']) * 100
            value_rate = (results['value_matches'] / results['total_chrome']) * 100
            logger.info(f"📈 Точных совпадений: {exact_rate:.1f}%")
            logger.info(f"📈 Совпадений по значению: {value_rate:.1f}%")
        
        # Показываем проблемы
        if results['missing_in_camoufox']:
            logger.warning(f"❌ Отсутствуют в Camoufox ({len(results['missing_in_camoufox'])}):")
            for cookie in results['missing_in_camoufox'][:5]:
                logger.warning(f"  - {cookie['domain']} | {cookie['name']} = {cookie['value_preview']}")
            if len(results['missing_in_camoufox']) > 5:
                logger.warning(f"  ... и еще {len(results['missing_in_camoufox']) - 5}")
        
        if results['value_mismatches']:
            logger.error(f"⚠️ Несоответствие значений ({len(results['value_mismatches'])}):")
            for mismatch in results['value_mismatches'][:3]:
                logger.error(f"  ! {mismatch['domain']} | {mismatch['name']}")
                logger.error(f"    Chrome:   {mismatch['chrome_value_preview']}")
                logger.error(f"    Camoufox: {mismatch['camoufox_value_preview']}")
        
        # Общая оценка
        if results['value_matches'] == results['total_chrome']:
            logger.success("🎉 Миграция куков прошла успешно!")
        elif results['value_matches'] >= results['total_chrome'] * 0.9:
            logger.info("✅ Миграция куков прошла хорошо (>90%)")
        elif results['value_matches'] >= results['total_chrome'] * 0.7:
            logger.warning("⚠️ Миграция куков прошла удовлетворительно (>70%)")
        else:
            logger.error("❌ Миграция куков требует внимания (<70%)")
    
    def test_specific_cookies(self, chrome_cookies: List[Dict], camoufox_cookies: List[Dict], test_domains: List[str]):
        """Тестировать куки для конкретных доменов"""
        logger.info(f"🧪 Тестируем куки для критических доменов: {', '.join(test_domains)}")
        
        chrome_normalized = self.normalize_chrome_cookies(chrome_cookies)
        camoufox_normalized = self.normalize_camoufox_cookies(camoufox_cookies)
        
        for domain in test_domains:
            logger.info(f"\n🎯 Тестируем домен: {domain}")
            
            chrome_domain_cookies = [c for c in chrome_normalized if domain in c['domain']]
            camoufox_domain_cookies = [c for c in camoufox_normalized if domain in c['domain']]
            
            if not chrome_domain_cookies:
                logger.info(f"  ℹ️ Нет куков для {domain} в Chrome")
                continue
            
            if not camoufox_domain_cookies:
                logger.error(f"  ❌ Куки для {domain} отсутствуют в Camoufox!")
                continue
            
            # Проверяем каждый кук
            chrome_index = {c['name']: c for c in chrome_domain_cookies}
            camoufox_index = {c['name']: c for c in camoufox_domain_cookies}
            
            matches = 0
            for name, chrome_cookie in chrome_index.items():
                if name in camoufox_index:
                    camoufox_cookie = camoufox_index[name]
                    if chrome_cookie['value'] == camoufox_cookie['value']:
                        matches += 1
                        logger.success(f"  ✅ {name}: Значения совпадают")
                    else:
                        logger.warning(f"  ⚠️ {name}: Значения различаются")
                else:
                    logger.error(f"  ❌ {name}: Отсутствует в Camoufox")
            
            success_rate = (matches / len(chrome_domain_cookies)) * 100 if chrome_domain_cookies else 0
            logger.info(f"  📊 Успешность для {domain}: {success_rate:.1f}% ({matches}/{len(chrome_domain_cookies)})")


async def main():
    parser = argparse.ArgumentParser(description='Продвинутая проверка корректности миграции куков')
    parser.add_argument('--chrome-profile', required=True, help='Путь к профилю Chrome')
    parser.add_argument('--camoufox-profile', required=True, help='ID профиля Camoufox')
    parser.add_argument('--test-domains', help='Домены для детального тестирования (через запятую)')
    parser.add_argument('--save-report', help='Сохранить отчет в файл')
    
    args = parser.parse_args()
    
    checker = AdvancedCookieMigrationChecker()
    
    # Получаем куки
    chrome_cookies = checker.get_chrome_cookies(args.chrome_profile)
    camoufox_cookies = checker.get_camoufox_cookies(args.camoufox_profile)
    
    if not chrome_cookies and not camoufox_cookies:
        logger.error("❌ Не удалось получить куки ни из одного источника")
        return
    
    # Выполняем продвинутое сравнение
    results = checker.compare_cookies_advanced(chrome_cookies, camoufox_cookies)
    checker.print_advanced_report(results)
    
    # Тестируем конкретные домены
    if args.test_domains:
        domains = [d.strip() for d in args.test_domains.split(',')]
        checker.test_specific_cookies(chrome_cookies, camoufox_cookies, domains)
    
    # Сохраняем отчет
    if args.save_report:
        report_data = {
            'timestamp': datetime.now().isoformat(),
            'chrome_profile': args.chrome_profile,
            'camoufox_profile': args.camoufox_profile,
            'results': results
        }
        
        with open(args.save_report, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)
        
        logger.success(f"📄 Отчет сохранен: {args.save_report}")


if __name__ == "__main__":
    asyncio.run(main()) 