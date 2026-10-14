-- weread-importer: cut-down weread.koplugin (import only).
local I18n = require("weread.lib.i18n")
local _ = function(text) return I18n.tr(text) end

return {
    fullname = _("WeRead 导入器"),
    description = _("导入 WeReadDownloader 生成的 .weread.zip。"),
    version = "1.0.0",
}
