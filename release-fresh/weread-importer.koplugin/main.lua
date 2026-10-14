-- weread-importer: cut-down build of weread.koplugin (same skeleton, same
-- menu mechanism, same import code path) -- no login, no reading, no sync.
--
-- Menu registration follows weread/ui/menu.lua exactly: addToMainMenu assigns
-- menu_items.<key> with sorting_hint = "tools", which is how the full plugin's
-- entry lands under the tools menu on this device.

local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local Content = require("weread.lib.content")
local DesktopBundle = require("weread.lib.desktop_bundle")
local ThoughtDB = require("weread.lib.thought_db")
local PluginUtil = require("weread.lib.plugin_util")

local Importer = WidgetContainer:extend{
    name = "weread-importer",
    is_doc_only = false,
    version = "1.0.0",
}

function Importer:init()
    -- registerToMainMenu in init(), exactly as the full plugin does. The
    -- menu builder calls addToMainMenu below for every registered widget.
    self.ui.menu:registerToMainMenu(self)
end

-- Same shape as weread/ui/menu.lua's addToMainMenu: assign a KEY on
-- menu_items (not :add, not table.insert) with sorting_hint = "tools".
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
                                        return DesktopBundle.import(
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
