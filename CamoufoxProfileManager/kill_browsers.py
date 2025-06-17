#!/usr/bin/env python3
"""
Скрипт для принудительного закрытия всех процессов Camoufox
"""

import psutil
import sys
import time
from loguru import logger


def kill_camoufox_processes():
    """Принудительно закрыть все процессы Camoufox"""
    logger.info("🔍 Поиск процессов Camoufox...")
    
    camoufox_processes = []
    
    # Ищем все процессы связанные с Camoufox
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            # Проверяем имя процесса и командную строку
            if proc.info['name'] and 'camoufox' in proc.info['name'].lower():
                camoufox_processes.append(proc)
            elif proc.info['cmdline']:
                cmdline = ' '.join(proc.info['cmdline']).lower()
                if 'camoufox' in cmdline:
                    camoufox_processes.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    
    if not camoufox_processes:
        logger.info("✅ Процессы Camoufox не найдены")
        return 0
    
    logger.info(f"🎯 Найдено {len(camoufox_processes)} процессов Camoufox")
    
    # Группируем процессы по родительским
    parent_processes = []
    child_processes = []
    
    for proc in camoufox_processes:
        try:
            # Если у процесса есть дочерние процессы Camoufox, это родительский процесс
            children = [p for p in proc.children() if p in camoufox_processes]
            if children:
                parent_processes.append(proc)
            else:
                child_processes.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            child_processes.append(proc)
    
    killed_count = 0
    
    # Сначала завершаем дочерние процессы
    if child_processes:
        logger.info(f"🔄 Завершение {len(child_processes)} дочерних процессов...")
        for proc in child_processes:
            try:
                logger.debug(f"Завершение дочернего процесса PID {proc.pid}: {proc.name()}")
                proc.terminate()
                killed_count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    
    # Ждем немного
    time.sleep(2)
    
    # Затем завершаем родительские процессы
    if parent_processes:
        logger.info(f"🔄 Завершение {len(parent_processes)} родительских процессов...")
        for proc in parent_processes:
            try:
                logger.debug(f"Завершение родительского процесса PID {proc.pid}: {proc.name()}")
                proc.terminate()
                killed_count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    
    # Ждем завершения
    time.sleep(3)
    
    # Принудительно убиваем оставшиеся процессы
    remaining_processes = []
    for proc in camoufox_processes:
        try:
            if proc.is_running():
                remaining_processes.append(proc)
        except psutil.NoSuchProcess:
            pass
    
    if remaining_processes:
        logger.warning(f"⚠️  Принудительное завершение {len(remaining_processes)} оставшихся процессов...")
        for proc in remaining_processes:
            try:
                logger.debug(f"Принудительное завершение PID {proc.pid}: {proc.name()}")
                proc.kill()
                killed_count += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    
    logger.success(f"✅ Завершено {killed_count} процессов Camoufox")
    return killed_count


def main():
    """Главная функция"""
    print("🦊 Camoufox Process Killer")
    print("=" * 40)
    
    try:
        killed_count = kill_camoufox_processes()
        
        if killed_count > 0:
            print(f"\n🎉 Успешно завершено {killed_count} процессов")
        else:
            print("\n💤 Активных процессов Camoufox не найдено")
            
    except KeyboardInterrupt:
        print("\n❌ Операция прервана пользователем")
        sys.exit(1)
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main() 