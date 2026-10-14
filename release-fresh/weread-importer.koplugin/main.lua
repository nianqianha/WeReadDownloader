-- weread-importer: import a desktop WeReadDownloader .weread.zip into
-- KOReader without installing the full WeRead client plugin.
--
-- Loader contract, established from four crash.log entries (2026-10-15):
-- the loader instantiates this plugin in the FILE MANAGER stage, where
-- self.ui.menu does not exist -- so init() touches nothing, and each menu
-- (file manager and reader) calls addMenuItems(menu) to fill itself. Entries
-- go in with table.insert; menu is the Menu widget instance both stages pass.

local InfoMessage = require("ui/widget/infomessage")
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local _ = require("gettext")

local Importer = WidgetContainer:extend{
    name = "weread-importer",
    is_doc_only = false,
    version = "1.0.0",
}

function Importer:addMenuItems(menu)
    table.insert(menu, {
        text = _("WeRead 导入器"),
        sub_item_table = {
            {
                text = _("导入桌面下载包"),
                keep_menu_open = false,
                callback = function()
                    local PathChooser = require("ui/widget/pathchooser")
                    local chooser = PathChooser:new{
                        title = _("选择 .weread.zip 导入包"),
                        show_hidden = false,
                        show_unsupported = true,
                        path = "/mnt/us/koreader/weread/bundle-inbox",
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
                                    text = _("导入完成！书已在 KOReader 书架中。"), })
                            end)
                        end,
                    }
                    UIManager:show(chooser)
                end,
            },
        },
    })
end

return Importer
