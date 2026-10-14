-- weread-importer: import a desktop WeReadDownloader .weread.zip into
-- KOReader without installing the full WeRead client plugin.
--
-- Everything under weread/lib is copied verbatim from weread.koplugin; the
-- only difference is the menu entry: this plugin adds an importer only -- no
-- login, no reading, no sync.

local InfoMessage = require(' + chr(34) + 'ui/widget/infomessage' + chr(34) + ')
local UIManager = require(' + chr(34) + 'ui/uimanager' + chr(34) + ')
local _ = require(' + chr(34) + 'gettext' + chr(34) + ')

local menu_entry = {
    text = _(' + chr(34) + 'Import desktop bundle' + chr(34) + '),
    keep_menu_open = false,
    callback = function()
        -- FileChooser built from a plugin crashes on filechooser.lua:336
        -- (self.ui == nil); PathChooser sets self.ui itself and is the
        -- supported way to pick a file from a plugin menu.
        local PathChooser = require(' + chr(34) + 'ui/widget/pathchooser' + chr(34) + ')
        local inbox = ' + chr(34) + '/mnt/us/koreader/weread/bundle-inbox' + chr(34) + '
        local chooser = PathChooser:new{
            title = _(' + chr(34) + 'Select .weread.zip' + chr(34) + '),
            show_hidden = false,
            show_unsupported = true,
            path = inbox,
            onConfirm = function(bundle_path)
                UIManager:nextTick(function()
                    local ok, result = pcall(function()
                        return require(' + chr(34) + 'weread.lib.desktop_bundle' + chr(34) + ').import(
                            G_reader_settings, bundle_path)
                    end)
                    if not ok then
                        UIManager:show(InfoMessage:new{
                            text = _(' + chr(34) + 'Import failed: ' + chr(34) + ') .. tostring(result), })
                        return
                    end
                    UIManager:show(InfoMessage:new{
                        text = _(' + chr(34) + 'Import finished: the book is in your KOReader library.' + chr(34) + '), })
                end)
            end,
        }
        UIManager:show(chooser)
    end,
}

return {
    name = ' + chr(34) + 'weread-importer' + chr(34) + ',
    fullname = _(' + chr(34) + 'WeRead Importer' + chr(34) + '),
    description = _(' + chr(34) + 'Import .weread.zip bundles from WeReadDownloader.' + chr(34) + '),
    version = ' + chr(34) + '1.0.0' + chr(34) + ',
    addToMainMenu = function(menu_table)
        table.insert(menu_table, {
            text = _(' + chr(34) + 'WeRead Importer' + chr(34) + '),
            sub_item_table = { menu_entry },
        })
    end,
}
