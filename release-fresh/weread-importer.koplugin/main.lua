-- weread-importer: import a desktop WeReadDownloader .weread.zip into
-- KOReader without installing the full WeRead client plugin.
--
-- Structure copied from the two working plugins on this device:
--   * weread.koplugin main.lua: class skeleton + init() with
--     registerToMainMenu + Mixin-injected addToMainMenu
--   * weread/ui/menu.lua: addToMainMenu body pattern (KEY ASSIGNMENT:
--     menu_items.<key> = { ... sorting_hint = "tools" })
-- The menu entry therefore lands in the TOOLS menu, next to WeRead itself.

local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local _ = require("gettext")

local Importer = WidgetContainer:extend{
    name = "weread-importer",
    is_doc_only = false,
    version = "1.0.0",
}

function Importer:init()
    self.ui.menu:registerToMainMenu(self)
end

function Importer:addToMainMenu(menu_items)
    menu_items.weread_importer = {
        text = _("WeRead 导入器"),
        sorting_hint = "tools",
        sub_item_table_func = function()
            return {
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
                                        UIManager:show(require("ui/widget/infomessage"):new{
                                            text = _("导入失败：")
                                                .. PluginUtil.display_error(result), })
                                        return
                                    end
                                    UIManager:show(require("ui/widget/infomessage"):new{
                                        text = _("导入完成！书已在 KOReader 书架中。"), })
                                end)
                            end,
                        }
                        UIManager:show(chooser)
                    end,
                },
            }
        end,
    }
end

return Importer
