# 简体中文与 Linux 输入法

在侧栏底部或登录页的 **Language / 界面语言** 中选择 **简体中文**。
选择会保存在当前浏览器中；首次访问会按浏览器的语言偏好选择中文或英文。
如果浏览器禁用了本地存储，切换仍在本次访问中有效。

界面翻译包括配置、分组、定时任务、回收站、备份和高级设置。
来自系统或上游的技术错误详情可能保留原文，便于排查。
这项设置控制管理器界面；每个配置的指纹语言、时区保持原样；它不会翻译 Camoufox 内核自身的菜单。

## Ubuntu 中文输入

先确认系统输入法能在其他 GTK 应用中输入中文，然后关闭并重新打开配置的浏览器。
源码中的 Linux 启动适配会：

- 为有窗口的浏览器默认关闭 `focusmanager.testmode`，让候选字正常提交。
- 从桌面打包程序启动时，为浏览器子进程恢复打包前的库路径，让 Fcitx 等输入法加载系统 GTK/GLib。

无需修改浏览器可执行文件或 Playwright 的安装文件。管理器进程继续使用自身的依赖。
通过 API 显式指定的 `firefox_user_prefs` 和 `env` 仍然优先；自定义环境时需自行保留显示服务、D-Bus 和输入法环境变量。

## Maintainer validation

The Linux workaround is applied in `BrowserSessionManager.launch`, including
launches from the API and scheduler. Headless launches keep Playwright's focus
defaults; non-Linux platforms are unaffected. Headed Linux profile launches
temporarily append the focus preference to the profile's `user.js` before
startup, then restores that file after launch or failure. This is necessary
with Playwright 1.60, which applies `firefox_user_prefs` after GTK has already
created its input context. In frozen Linux builds, the child environment
restores `LD_LIBRARY_PATH_ORIG`, or removes `LD_LIBRARY_PATH` when no original
path exists, without modifying the manager's environment.

To verify native input, use an unmodified Playwright installation and browser
binary, launch a temporary profile from source, and type `nihao` with Fcitx5
Pinyin in a local page's textarea. Selecting the candidate should emit
`compositionend` and leave `你好` in the textarea. Test with a frozen-style
library path as well as a normal source launch. DOM `fill()` or synthetic
JavaScript composition events do not exercise the native input-method path.

The related upstream discussion is [Camoufox #266](https://github.com/daijro/camoufox/issues/266).
