#!/usr/bin/env python3
"""
Утилита для проверки корректности миграции куков между Chrome и Camoufox
"""

import asyncio
import sqlite3
import json
import os
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime
import argparse

from core.chrome_cookie_decryptor import ChromeCookieDecryptor
from core.profile_manager import ProfileManager
from core.database import DatabaseManager
from loguru import logger


class CookieMigrationChecker:
    """Класс для проверки корректности миграции куков"""
    
    def __init__(self):
        self.chrome_decryptor = ChromeCookieDecryptor()
        
    def get_chrome_cookies(self, chrome_profile_path: str) -> List[Dict[str, Any]]:
        """Получить куки из профиля Chrome"""
        logger.info(f"🔍 Читаем куки из Chrome профиля: {chrome_profile_path}")
        
        cookies_db_path = os.path.join(chrome_profile_path, "Cookies")
        if not os.path.exists(cookies_db_path):
            logger.error(f"❌ Файл куков не найден: {cookies_db_path}")
            return []
        
        try:
            # Используем встроенный метод для извлечения куков
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
            
            # Читаем куки из Firefox/Camoufox формата
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
    
    def _normalize_chrome_cookies(self, chrome_cookies: List[Dict]) -> List[Dict]:
        """Нормализовать структуру куков Chrome для сравнения"""
        normalized = []
        for cookie in chrome_cookies:
            normalized_cookie = {
                'name': cookie.get('name', ''),
                'value': cookie.get('value', ''),
                'domain': cookie.get('host_key', ''),
                'path': cookie.get('path', '/'),
                'expires': cookie.get('expires_utc'),
                'secure': cookie.get('is_secure', False),
                'httponly': cookie.get('is_httponly', False),
                'samesite': cookie.get('samesite', 0)
            }
            normalized.append(normalized_cookie)
        return normalized
    
    def _normalize_camoufox_cookies(self, camoufox_cookies: List[Dict]) -> List[Dict]:
        """Нормализовать структуру куков Camoufox для сравнения"""
        normalized = []
        for cookie in camoufox_cookies:
            normalized_cookie = {
                'name': cookie.get('name', ''),
                'value': cookie.get('value', ''),
                'domain': cookie.get('domain', ''),
                'path': cookie.get('path', '/'),
                'expires': cookie.get('expires'),
                'secure': cookie.get('secure', False),
                'httponly': cookie.get('httponly', False),
                'samesite': cookie.get('samesite', 'unspecified')
            }
            normalized.append(normalized_cookie)
        return normalized
    
    def compare_cookies(self, chrome_cookies: List[Dict], camoufox_cookies: List[Dict]) -> Dict[str, Any]:
        """Сравнить куки между Chrome и Camoufox"""
        logger.info("🔄 Сравниваем куки между Chrome и Camoufox...")
        
        # Нормализуем структуру куков для сравнения
        chrome_normalized = self._normalize_chrome_cookies(chrome_cookies)
        camoufox_normalized = self._normalize_camoufox_cookies(camoufox_cookies)
        
        # Создаем индексы для быстрого поиска
        chrome_index = {f"{c['domain']}:{c['name']}": c for c in chrome_normalized}
        camoufox_index = {f"{c['domain']}:{c['name']}": c for c in camoufox_normalized}
        
        results = {
            'total_chrome': len(chrome_cookies),
            'total_camoufox': len(camoufox_cookies),
            'matched': 0,
            'missing_in_camoufox': [],
            'extra_in_camoufox': [],
            'value_mismatches': [],
            'attribute_mismatches': []
        }
        
        # Проверяем куки из Chrome
        for key, chrome_cookie in chrome_index.items():
            if key in camoufox_index:
                camoufox_cookie = camoufox_index[key]
                results['matched'] += 1
                
                # Сравниваем значения
                if chrome_cookie['value'] != camoufox_cookie['value']:
                    results['value_mismatches'].append({
                        'domain': chrome_cookie['domain'],
                        'name': chrome_cookie['name'],
                        'chrome_value': chrome_cookie['value'][:50] + '...' if len(chrome_cookie['value']) > 50 else chrome_cookie['value'],
                        'camoufox_value': camoufox_cookie['value'][:50] + '...' if len(camoufox_cookie['value']) > 50 else camoufox_cookie['value']
                    })
                
                # Сравниваем атрибуты
                attr_diffs = []
                if chrome_cookie.get('secure', False) != camoufox_cookie.get('secure', False):
                    attr_diffs.append('secure')
                if chrome_cookie.get('httponly', False) != camoufox_cookie.get('httponly', False):
                    attr_diffs.append('httponly')
                if chrome_cookie.get('path', '/') != camoufox_cookie.get('path', '/'):
                    attr_diffs.append('path')
                
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
                    'value': chrome_cookie['value'][:50] + '...' if len(chrome_cookie['value']) > 50 else chrome_cookie['value']
                })
        
        # Проверяем лишние куки в Camoufox
        for key, camoufox_cookie in camoufox_index.items():
            if key not in chrome_index:
                results['extra_in_camoufox'].append({
                    'domain': camoufox_cookie['domain'],
                    'name': camoufox_cookie['name'],
                    'value': camoufox_cookie['value'][:50] + '...' if len(camoufox_cookie['value']) > 50 else camoufox_cookie['value']
                })
        
        return results
    
    def print_comparison_report(self, results: Dict[str, Any]):
        """Вывести отчет о сравнении куков"""
        logger.info("📊 Отчет о сравнении куков:")
        logger.info(f"Chrome куков: {results['total_chrome']}")
        logger.info(f"Camoufox куков: {results['total_camoufox']}")
        logger.info(f"Совпадающих: {results['matched']}")
        
        if results['missing_in_camoufox']:
            logger.warning(f"❌ Отсутствуют в Camoufox ({len(results['missing_in_camoufox'])}):")
            for cookie in results['missing_in_camoufox'][:10]:  # Показываем первые 10
                logger.warning(f"  - {cookie['domain']} | {cookie['name']} = {cookie['value']}")
            if len(results['missing_in_camoufox']) > 10:
                logger.warning(f"  ... и еще {len(results['missing_in_camoufox']) - 10}")
        
        if results['extra_in_camoufox']:
            logger.info(f"➕ Дополнительные в Camoufox ({len(results['extra_in_camoufox'])}):")
            for cookie in results['extra_in_camoufox'][:5]:  # Показываем первые 5
                logger.info(f"  + {cookie['domain']} | {cookie['name']} = {cookie['value']}")
        
        if results['value_mismatches']:
            logger.error(f"⚠️ Несоответствие значений ({len(results['value_mismatches'])}):")
            for mismatch in results['value_mismatches'][:5]:  # Показываем первые 5
                logger.error(f"  ! {mismatch['domain']} | {mismatch['name']}")
                logger.error(f"    Chrome:   {mismatch['chrome_value']}")
                logger.error(f"    Camoufox: {mismatch['camoufox_value']}")
        
        if results['attribute_mismatches']:
            logger.warning(f"🔧 Несоответствие атрибутов ({len(results['attribute_mismatches'])}):")
            for mismatch in results['attribute_mismatches'][:5]:
                logger.warning(f"  ~ {mismatch['domain']} | {mismatch['name']} | {', '.join(mismatch['differences'])}")
        
        # Вычисляем процент успешности
        if results['total_chrome'] > 0:
            success_rate = (results['matched'] / results['total_chrome']) * 100
            logger.info(f"📈 Процент успешной миграции: {success_rate:.1f}%")
        
        return results
    
    def analyze_specific_domains(self, chrome_cookies: List[Dict], camoufox_cookies: List[Dict], domains: List[str]):
        """Анализ куков для конкретных доменов"""
        logger.info(f"🎯 Анализ куков для доменов: {', '.join(domains)}")
        
        # Нормализуем куки для анализа
        chrome_normalized = self._normalize_chrome_cookies(chrome_cookies)
        camoufox_normalized = self._normalize_camoufox_cookies(camoufox_cookies)
        
        for domain in domains:
            logger.info(f"\n🌐 Домен: {domain}")
            
            # Ищем куки для этого домена
            chrome_domain_cookies = [c for c in chrome_normalized if domain in c['domain']]
            camoufox_domain_cookies = [c for c in camoufox_normalized if domain in c['domain']]
            
            logger.info(f"  Chrome: {len(chrome_domain_cookies)} куков")
            logger.info(f"  Camoufox: {len(camoufox_domain_cookies)} куков")
            
            if chrome_domain_cookies:
                logger.info("  Chrome куки:")
                for cookie in chrome_domain_cookies:
                    value_preview = cookie['value'][:30] + '...' if len(cookie['value']) > 30 else cookie['value']
                    logger.info(f"    {cookie['name']} = {value_preview}")
            
            if camoufox_domain_cookies:
                logger.info("  Camoufox куки:")
                for cookie in camoufox_domain_cookies:
                    value_preview = cookie['value'][:30] + '...' if len(cookie['value']) > 30 else cookie['value']
                    logger.info(f"    {cookie['name']} = {value_preview}")


async def main():
    parser = argparse.ArgumentParser(description='Проверка корректности миграции куков')
    parser.add_argument('--chrome-profile', required=True, help='Путь к профилю Chrome')
    parser.add_argument('--camoufox-profile', required=True, help='ID профиля Camoufox')
    parser.add_argument('--domains', help='Домены для детального анализа (через запятую)')
    parser.add_argument('--save-report', help='Сохранить отчет в файл')
    
    args = parser.parse_args()
    
    checker = CookieMigrationChecker()
    
    # Получаем куки из обоих источников
    chrome_cookies = checker.get_chrome_cookies(args.chrome_profile)
    camoufox_cookies = checker.get_camoufox_cookies(args.camoufox_profile)
    
    if not chrome_cookies and not camoufox_cookies:
        logger.error("❌ Не удалось получить куки ни из одного источника")
        return
    
    # Сравниваем куки
    results = checker.compare_cookies(chrome_cookies, camoufox_cookies)
    checker.print_comparison_report(results)
    
    # Анализ для конкретных доменов
    if args.domains:
        domains = [d.strip() for d in args.domains.split(',')]
        checker.analyze_specific_domains(chrome_cookies, camoufox_cookies, domains)
    
    # Сохраняем отчет
    if args.save_report:
        report_data = {
            'timestamp': datetime.now().isoformat(),
            'chrome_profile': args.chrome_profile,
            'camoufox_profile': args.camoufox_profile,
            'results': results,
            'chrome_cookies': chrome_cookies,
            'camoufox_cookies': camoufox_cookies
        }
        
        with open(args.save_report, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)
        
        logger.success(f"📄 Отчет сохранен: {args.save_report}")


if __name__ == "__main__":
    asyncio.run(main()) 