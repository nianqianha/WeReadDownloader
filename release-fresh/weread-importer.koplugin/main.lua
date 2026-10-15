-- weread-importer: import a desktop WeReadDownloader export into KOReader
-- without installing the full WeRead client plugin.
--
-- Menu structure copied from the two working plugins on this device:
--   * weread.koplugin main.lua: class skeleton + init() with
--     registerToMainMenu
--   * weread/ui/menu.lua: addToMainMenu body pattern (KEY ASSIGNMENT:
--     menu_items.<key> = { ... sorting_hint = "tools" })
-- The entry lands in the TOOLS menu, next to WeRead itself.
--
-- The PC end exports an UNPACKED directory (bundle.json, manifest.json,
-- reviews.jsonl, and the .epub side by side); the import path touches no
-- FFI -- this device's libarchive crashes in every extraction mode
-- (traced 2026-10-15, four crashes in four positions).

local InfoMessage = require("ui/widget/infomessage")
local UIManager = require("ui/uimanager")
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local _ = require("gettext")
local PluginUtil = require("weread_importer_lib.plugin_util")

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
                            title = _("选择解压后的导入文件夹"),
                            show_hidden = false,
                            show_unsupported = true,
                            path = "/mnt/us/koreader/weread/bundle-inbox",
                            onConfirm = function(bundle_path)
                                UIManager:nextTick(function()
                                    -- zip files are not supported on this
                                    -- device: libarchive crashes on extract.
                                    if tostring(bundle_path):match("%.zip$") then
                                        UIManager:show(InfoMessage:new{
                                            text = _("请选择解压后的文件夹（bundle-inbox 里同名目录），不要选 .zip 文件。"), })
                                        return
                                    end
                                    local ok, result = pcall(function()
                                        return require("weread_importer_lib.desktop_bundle").import(
                                            G_reader_settings, bundle_path)
                                    end)
                                    if not ok then
                                        UIManager:show(InfoMessage:new{
                                            text = _("导入失败：")
                                                .. PluginUtil.display_error(result), })
                                        return
                                    end
                                    -- Refresh whatever UI is open so the
                                    -- new book shows up WITHOUT a restart.
                                    pcall(function()
                                        local FileManager =
                                            require("apps/filemanager/filemanager")
                                        if FileManager.instance then
                                            FileManager.instance:refreshPath(
                                                "/mnt/us/documents/weread-imports")
                                        end
                                    end)
                                    local msg = _("导入完成！《")
                                        .. tostring(result.title)
                                        .. _("》已在书库中。")
                                    if result.thoughts == "imported" then
                                        msg = msg .. "\n划线和想法已导入。"
                                    else
                                        msg = msg .. "\n（想法数据：" 
                                            .. tostring(result.thoughts) .. "）"
                                    end
                                    UIManager:show(InfoMessage:new{ text = msg })
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
