-- weread-importer: import a desktop WeReadDownloader .weread.zip into
-- KOReader without installing the full WeRead client plugin.
--
-- Two loader contracts learned from crash.log:
--   02:41:22  "attempt to call a nil value"  -> addToMainMenu must be a METHOD
--             on a WidgetContainer subclass, not a function field on a table
--   02:50:33  "failed to register widget filemanagerweread-importer ... :25"
--             -> init() runs in the FILE MANAGER stage, where self.ui.menu
--             does not exist; registerToMainMenu belongs to reader-stage
--             plugins whose init runs later. A plugin that wants to appear in
--             BOTH menus implements only the addToMainMenu method and lets the
--             menu builder call it.

local InfoMessage = require("ui/widget/infomessage")
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local _ = require("gettext")

local Importer = WidgetContainer:extend{
    name = "weread-importer",
    is_doc_only = false,
    version = "1.0.0",
}

function Importer:addToMainMenu(menu_table)
    menu_table:add{
        text = _("WeRead Importer"),
        sub_item_table = {
            {
                text = _("Import desktop bundle"),
                keep_menu_open = false,
                callback = function()
                    local PathChooser = require("ui/widget/pathchooser")
                    local inbox = "/mnt/us/koreader/weread/bundle-inbox"
                    local chooser = PathChooser:new{
                        title = _("Select .weread.zip"),
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
                                        text = _("Import failed: ")
                                            .. tostring(result), })
                                    return
                                end
                                UIManager:show(InfoMessage:new{
                                    text = _("Import finished: the book is in "
                                        .. "your KOReader library."), })
                            end)
                        end,
                    }
                    UIManager:show(chooser)
                end,
            },
        },
    }
end

return Importer
