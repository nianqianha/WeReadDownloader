-- weread-importer: import a desktop WeReadDownloader .weread.zip into
-- KOReader without installing the full WeRead client plugin.
--
-- addToMainMenu receives a PLAIN ARRAY (filemanagermenu.lua builds one and
-- calls this method on every registered widget): entries go in with
-- table.insert. Two earlier attempts failed on exactly this:
--   menu_items:add{...}            -> "attempt to call method 'add'"
--   Menu.addToMainMenu(...)        -> ui/widget/menu has no such static
-- (lightnovel's Menu.addToMainMenu is lightnovel's OWN module, not this one.)

local InfoMessage = require("ui/widget/infomessage")
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
    table.insert(menu_items, {
        text = _("WeRead 导入器"),
        sub_item_table = {
            {
                text = _("导入桌面下载包"),
                keep_menu_open = false,
                callback = function()
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
