-- weread-importer: import a desktop WeReadDownloader .weread.zip into
-- KOReader without installing the full WeRead client plugin.
--
-- Shape matters: KOReader's loader instantiates the returned WidgetContainer
-- subclass and calls plugin:addToMainMenu(menu_table) as a METHOD. The first
-- attempt returned a plain table with a function field, and the colon-call
-- died with "attempt to call a nil value" (crash.log 2026-10-15 02:41:22).

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

function Importer:addToMainMenu(menu_table)
    menu_table:add{
        text = _("WeRead Importer"),
        sub_item_table = {
            {
                text = _("Import desktop bundle"),
                keep_menu_open = false,
                callback = function()
                    -- FileChooser built from a plugin crashes on
                    -- filechooser.lua:336 (self.ui == nil); PathChooser sets
                    -- self.ui itself and is the supported way to pick from a
                    -- plugin menu.
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
