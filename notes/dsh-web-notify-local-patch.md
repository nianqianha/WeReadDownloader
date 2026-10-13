# dsh-web-notify 本地补丁（重 clone 后需重打）

桌面 `dsh-plugins-install/` 已清理，四个插件均可重新 clone。
其中 `dsh-web-notify` 有**一处实质本地改动**，重 clone 后会丢失：

仓库: https://github.com/Wepoi9/dsh-web-notify

文件: `package-lock.json`（及对应的 `package.json` engines 字段）

改动: 在 `engines.dsh` 白名单末尾追加 `|| 0.2.1-alpha.2`，
使插件能在本机已安装的 DSH 0.2.1-alpha.2 下加载。

重打方法:
```
git clone https://github.com/Wepoi9/dsh-web-notify
cd dsh-web-notify
# 在 package.json 与 package-lock.json 的 engines.dsh 里加上 || 0.2.1-alpha.2
```

另三个插件本地干净、无改动，直接 clone 即可：
- dsh-approval-chime  https://github.com/Cloudto1/dsh-approval-chime
- dsh-notify          https://github.com/Zhiyi-Zhao/dsh-notify
- dsh-voice-alert     https://github.com/BUGLAN/dsh-voice-alert
