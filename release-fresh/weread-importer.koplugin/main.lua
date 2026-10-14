-- weread-importer: import a desktop WeReadDownloader .weread.zip into
-- KOReader without installing the full WeRead client plugin.
--
-- Everything under weread/lib is copied verbatim from weread.koplugin (same
-- commit); the only behavioural difference is the menu entry: this plugin adds
-- 导入工具 only, no login, no reading, no sync.

local InfoMessage = require("ui/widget/infomessage")
local UIManager = require("ui/uimanager")
local _ = require("gettext")

local menu_entry = {
    text = _("导入桌面下载包"),
    keep_menu_open = false,
    callback = function()
        -- the exact picker flow the full plugin uses (menu.lua, see the
        -- PathChooser rationale there): FileChooser built from a plugin
        -- crashes on filechooser.lua:336 (self.ui == nil); PathChooser sets
        -- self.ui itself and is the supported way to pick from a plugin.
        local PathChooser = require("ui/widget/pathchooser")
        local inbox = "/mnt/us/koreader/weread/bundle-inbox"
        local chooser = PathChooser:new{
            title = _("选择 .weread.zip 导入包"),
            show_hidden = false,
            show_unsupported = true,
            path = inbox,
            onConfirm = function(bundle_path)
                UIManager:nextTick(function()
                    local ok, result = pcall(function()
                        return require("weread.lib.desktop_bundle").import(
                            G_reader_settings, bundle_path)
                    end)
                    if not ok then
                        UIManager:show(InfoMessage:new{
                            text = _("导入失败：") .. tostring(result), })
                        return
                    end
                    UIManager:show(InfoMessage:new{
                        text = _("导入完成，书已在 KOReader 书架中。"), })
                end)
            end,
        }
        UIManager:show(chooser)
    end,
}

return {
    name = "weread-importer",
    fullname = _("WeRead 导入工具"),
    description = _([[导入 WeReadDownloader 在电脑上生成的 .weread.zip 导入包。]]),
    -- main menu entry, so the user does not have to know where else to look
    addToMainMenu = function(menu_table)
        table.insert(menu_table, {
            text = _("WeRead 导入工具"),
            sub_item_table = { menu_entry },
        })
    end,
}
