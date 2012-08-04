"""!
@package vnet.dialogs

@brief Dialog for vector network analysis front-end

Classes:
 - dialogs::VNETDialog
 - dialogs::PtsList
 - dialogs::SettingsDialog
 - dialogs::VnetTmpVectMaps
 - dialogs::VectMap
 - dialogs::History

(C) 2012 by the GRASS Development Team

This program is free software under the GNU General Public License
(>=v2). Read the file COPYING that comes with GRASS for details.

@author Stepan Turek <stepan.turek seznam.cz> (GSoC 2012, mentor: Martin Landa)
"""

import os
import sys
import types
try:
    import grass.lib.vector as vectlib
    from ctypes import pointer, byref, c_char_p, c_int, c_double
    haveCtypes = True
except ImportError:
    haveCtypes = False

from copy import copy
from grass.script     import core as grass

import wx
import wx.aui
import wx.lib.flatnotebook  as FN
import wx.lib.colourselect as csel

from core             import globalvar, utils
from core.settings    import UserSettings
from core.gcmd        import RunCommand, GMessage

from gui_core.widgets import GNotebook
from gui_core.goutput import GMConsole, CmdThread, EVT_CMD_DONE
from gui_core.gselect import Select, LayerSelect, ColumnSelect

from vnet.widgets     import PointsList
from vnet.toolbars    import MainToolbar, PointListToolbar

#Main TODOs
# when layer tree is lmgr is changed, tmp layer is removed from render list 
# optimization of map drawing 
# static box placement??
# tmp maps add number of process
# destructor problem


class VNETDialog(wx.Dialog):
    def __init__(self, parent,
                 id = wx.ID_ANY, title = _("Vector network analysis"),
                 style = wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, **kwargs):
        """!Dialog for vector network analysis"""

        wx.Dialog.__init__(self, parent, id, style=style, title = title, **kwargs)

        self.parent  = parent  # mapdisp.frame MapFrame
        self.mapWin = parent.MapWindow
        self.inputData = {}
        self.cmdParams = {}
        self.snapData = {}
        self.snapping = False
        self.tmp_result = None

        self.history = History(self)
        self.histTmpVectMapNum = 0
        self.tmpVectMapsToHist = []

        self.tmpMaps = VnetTmpVectMaps(parent = self)

        self._initSettings()

        # registration graphics for drawing
        self.pointsToDraw = self.mapWin.RegisterGraphicsToDraw(graphicsType = "point", 
                                                               setStatusFunc = self.SetPointStatus)
        self.SetPointDrawSettings()

        # getting attribute table columns only with numbers (costs)
        self.columnTypes = ['integer', 'double precision'] 

        self.SetIcon(wx.Icon(os.path.join(globalvar.ETCICONDIR, 'grass_map.ico'), wx.BITMAP_TYPE_ICO))
        
        # initialization of v.net.* analysis parameters
        self._initVnetParams()

        # toobars
        self.toolbars = {}
        self.toolbars['mainToolbar'] = MainToolbar(parent = self)

        #
        # Fancy gui
        #
        self._mgr = wx.aui.AuiManager(self)

        # Columns in points list
        self.cols =   [
                        ['type', ["", _("Start point"), _("End point")], ""], #TODO init dynamically, translation problem
                        ['topology', None, ""] 
                      ]

        self.mainPanel = wx.Panel(parent=self)
        self.notebook = GNotebook(parent = self.mainPanel,
                                  style = FN.FNB_FANCY_TABS | FN.FNB_BOTTOM |
                                          FN.FNB_NO_NAV_BUTTONS | FN.FNB_NO_X_BUTTON)

        # Creates tabs
        self._createPointsPage()
        self._createParametersPage()
        self._createOutputPage()

        self._addPanes()
        self._doDialogLayout()

        self._mgr.Update()

        self.handlerRegistered = False
        self.tmpResultLayer = None 

        # adds 2 points into list
        for i in range(2):
            self.list.AddItem()
            colNum = self.list.GetColumnNum('type')
            self.list.EditCellIndex(i, colNum, self.cols[1][1][1 + i]) 
            self.list.CheckItem(i, True)

        # selects first point
        self.list.selected = 0
        self.list.Select(self.list.selected)

        self.Bind(wx.EVT_CLOSE, self.OnCloseDialog)

        dlgSize = (410, 520)
        self.SetMinSize(dlgSize)
        self.SetInitialSize(dlgSize)

        #fix goutput's pane size (required for Mac OSX)
        if self.goutput:         
            self.goutput.SetSashPosition(int(self.GetSize()[1] * .75))

        self.OnAnalysisChanged(None)
        self.notebook.SetSelectionByName("parameters")

    def  __del__(self):
        """!Removes temp layers, unregisters handlers and graphics"""

        update = self.tmpMaps.DeleteAllTmpMaps()

        self.mapWin.UnregisterGraphicsToDraw(self.pointsToDraw)

        if self.handlerRegistered:
            self.mapWin.UnregisterMouseEventHandler(wx.EVT_LEFT_DOWN, 
                                                  self.OnMapClickHandler)
        if update:
            self.mapWin.UpdateMap(render=True, renderVector=True)
        else:
            self.mapWin.UpdateMap(render=False, renderVector=False)


    def _addPanes(self):
        """!Adds toolbar pane and pane with tabs"""

        self._mgr.AddPane(self.toolbars['mainToolbar'],
                              wx.aui.AuiPaneInfo().
                              Name("pointlisttools").Caption(_("Point list toolbar")).
                              ToolbarPane().Top().
                              Dockable(False).
                              CloseButton(False).Layer(0))

        self._mgr.AddPane(self.mainPanel,
                              wx.aui.AuiPaneInfo().
                              Name("tabs").CaptionVisible(visible = False).
                              Center().
                              Dockable(False).
                              CloseButton(False).Layer(0))

    def _doDialogLayout(self):

        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(item = self.notebook, proportion = 1,
                  flag = wx.EXPAND)
        
        self.mainPanel.SetSizer(sizer)

        sizer.Fit(self)  
        self.Layout()

    def _createPointsPage(self):
        """!Tab with points list and analysis settings"""

        pointsPanel = wx.Panel(parent = self)
        self.anSettings = {} #TODO
        maxValue = 1e8

        listBox = wx.StaticBox(parent = pointsPanel, id = wx.ID_ANY,
                                label =" %s " % _("Points for analysis:"))

        self.notebook.AddPage(page = pointsPanel, 
                              text=_('Points'), 
                              name = 'points')

        self.list = PtsList(parent = pointsPanel, dialog = self, cols = self.cols)
        self.toolbars['pointsList'] = PointListToolbar(parent = pointsPanel, list = self.list)

        anSettingsPanel = wx.Panel(parent = pointsPanel)

        anSettingsBox = wx.StaticBox(parent = anSettingsPanel, id = wx.ID_ANY,
                                label =" %s " % _("Analysis settings:"))

        #lineIdPanel =  wx.Panel(parent = anSettingsPanel)
        #lineIdLabel = wx.StaticText(parent = lineIdPanel, id = wx.ID_ANY, label =_("Id of line:"))
        #elf.anSettings["line_id"] = wx.SpinCtrl(parent = lineIdPanel, id = wx.ID_ANY, min = 1, max = maxValue)
        #resId = int(UserSettings.Get(group ='vnet', key ='analysis_settings', subkey = 'resultId'))
        #self.anSettings["line_id"].SetValue(resId)

        maxDistPanel =  wx.Panel(parent = anSettingsPanel)
        maxDistLabel = wx.StaticText(parent = maxDistPanel, id = wx.ID_ANY, label = _("Maximum distance of point to the network:"))
        self.anSettings["max_dist"] = wx.SpinCtrl(parent = maxDistPanel, id = wx.ID_ANY, min = 0, max = maxValue) #TODO
        #maxDist = int(UserSettings.Get(group = 'vnet', key = 'analysis_settings', subkey ='maxDist'))
        self.anSettings["max_dist"].SetValue(100000) #TODO init val

        #showCutPanel =  wx.Panel(parent = anSettingsPanel)
        #self.anSettings["show_cut"] = wx.CheckBox(parent = showCutPanel, id=wx.ID_ANY,
        #                                          label = _("Show minimal cut"))
        #self.anSettings["show_cut"].Bind(wx.EVT_CHECKBOX, self.OnShowCut)

        isoLinesPanel =  wx.Panel(parent = anSettingsPanel)
        isoLineslabel = wx.StaticText(parent = isoLinesPanel, id = wx.ID_ANY, label = _("Iso lines:"))
        self.anSettings["iso_lines"] = wx.TextCtrl(parent = isoLinesPanel, id = wx.ID_ANY) #TODO
        self.anSettings["iso_lines"].SetValue("1000,2000,3000")

        # Layout
        AnalysisSizer = wx.BoxSizer(wx.VERTICAL)

        listSizer = wx.StaticBoxSizer(listBox, wx.VERTICAL)

        listSizer.Add(item = self.toolbars['pointsList'], proportion = 0)
        listSizer.Add(item = self.list, proportion = 1, flag = wx.EXPAND)

        anSettingsSizer = wx.StaticBoxSizer(anSettingsBox, wx.VERTICAL)

        #lineIdSizer = wx.BoxSizer(wx.HORIZONTAL)
        #lineIdSizer.Add(item = lineIdLabel, flag = wx.EXPAND | wx.ALL, border = 5, proportion = 0)
        #lineIdSizer.Add(item = self.anSettings["line_id"],
        #                flag = wx.EXPAND | wx.ALL, border = 5, proportion = 0)
        #lineIdPanel.SetSizer(lineIdSizer)
        #anSettingsSizer.Add(item = lineIdPanel, proportion = 1, flag = wx.EXPAND)

        maxDistSizer = wx.BoxSizer(wx.HORIZONTAL)
        maxDistSizer.Add(item = maxDistLabel, flag = wx.ALIGN_CENTER_VERTICAL, proportion = 1)
        maxDistSizer.Add(item = self.anSettings["max_dist"],
                         flag = wx.EXPAND | wx.ALL, border = 5, proportion = 0)
        maxDistPanel.SetSizer(maxDistSizer)
        anSettingsSizer.Add(item = maxDistPanel, proportion = 1, flag = wx.EXPAND)

        #showCutSizer = wx.BoxSizer(wx.HORIZONTAL)
        #showCutPanel.SetSizer(showCutSizer)
        #showCutSizer.Add(item = self.anSettings["show_cut"],
        #                 flag = wx.EXPAND | wx.ALL, border = 5, proportion = 0)
        #anSettingsSizer.Add(item = showCutPanel, proportion = 1, flag = wx.EXPAND)

        isoLinesSizer = wx.BoxSizer(wx.HORIZONTAL)
        isoLinesSizer.Add(item = isoLineslabel, flag = wx.ALIGN_CENTER_VERTICAL, proportion = 1)
        isoLinesSizer.Add(item = self.anSettings["iso_lines"],
                        flag = wx.EXPAND | wx.ALL, border = 5, proportion = 1)
        isoLinesPanel.SetSizer(isoLinesSizer)
        anSettingsSizer.Add(item = isoLinesPanel, proportion = 1, flag = wx.EXPAND)

        AnalysisSizer.Add(item = listSizer, proportion = 1, flag = wx.EXPAND | wx.ALL, border = 5)
        AnalysisSizer.Add(item = anSettingsPanel, proportion = 0, flag = wx.EXPAND | wx.RIGHT | wx.LEFT | wx.BOTTOM, border = 5)

        anSettingsPanel.SetSizer(anSettingsSizer)
        pointsPanel.SetSizer(AnalysisSizer)

    def OnShowCut(self, event):
        """!Shows vector map with minimal cut (v.net.flow) - not yet implemented"""
        val = event.IsChecked()
        if val:
            self.tmp_result.DeleteRenderLayer()
            cmd = self.GetLayerStyle()
            self.vnetFlowTmpCut.AddRenderLayer(cmd)
        else:
            self.vnetFlowTmpCut.DeleteRenderLayer()
            cmd = self.GetLayerStyle()
            self.tmp_result.AddRenderLayer(cmd)

        self.mapWin.UpdateMap(render = True, renderVector = True)

    def _createOutputPage(self):
        """!Tab with output console"""
        outputPanel = wx.Panel(parent = self)
        self.notebook.AddPage(page = outputPanel, 
                              text = _("Output"), 
                              name = 'output')

        #TODO ugly hacks - just for GMConsole to be happy 
        self.notebook.notebookpanel = CmdPanelHack()
        outputPanel.notebook = self.notebook # for GMConsole init
        outputPanel.parent = self.notebook # for GMConsole OnDone

        self.goutput = GMConsole(parent = outputPanel, margin = False)

        self.outputSizer = wx.BoxSizer(wx.VERTICAL)

        self.outputSizer.Add(item = self.goutput, proportion = 1, flag = wx.EXPAND)
        # overridden outputSizer.SetSizeHints(self) in GMConsole _layout
        self.goutput.SetMinSize((-1,-1))

        outputPanel.SetSizer(self.outputSizer)

    def _createParametersPage(self):
        """!Tab with output console"""
        dataPanel = wx.Panel(parent=self)
        self.notebook.AddPage(page = dataPanel,
                              text=_('Parameters'), 
                              name = 'parameters')
        label = {}
        dataSelects = [
                        ['input', "Choose vector map for analysis:", Select],
                        ['alayer', "Arc layer number or name:", LayerSelect],
                        ['nlayer', "Node layer number or name:", LayerSelect],
                        ['afcolumn', self.attrCols['afcolumn']['label'], ColumnSelect],
                        ['abcolumn', self.attrCols['abcolumn']['label'], ColumnSelect],
                        ['ncolumn', self.attrCols['ncolumn']['label'], ColumnSelect],
                      ]

        selPanels = {}
        for dataSel in dataSelects:
            selPanels[dataSel[0]] = wx.Panel(parent = dataPanel)
            if dataSel[0] == 'input' and self.mapWin.tree:
                self.inputData[dataSel[0]] = dataSel[2](parent = selPanels[dataSel[0]],  
                                                        size = (-1, -1), 
                                                        type = 'vector')

                icon = wx.Image(os.path.join(globalvar.ETCICONDIR, "grass", "layer-vector-add.png"))
                icon.Rescale(18, 18)
                icon = wx.BitmapFromImage(icon) 
                self.addToTreeBtn = wx.BitmapButton(parent = selPanels[dataSel[0]], 
                                                    bitmap = icon, 
                                                    size = globalvar.DIALOG_COLOR_SIZE) 
                self.addToTreeBtn.SetToolTipString(_("Add vector map into layer tree"))
                self.addToTreeBtn.Disable()
                self.addToTreeBtn .Bind(wx.EVT_BUTTON, self.OnToTreeBtn)
            else:
                self.inputData[dataSel[0]] = dataSel[2](parent = selPanels[dataSel[0]],  
                                                        size = (-1, -1))
            label[dataSel[0]] =  wx.StaticText(parent =  selPanels[dataSel[0]], 
                                               name = dataSel[0])
            label[dataSel[0]].SetLabel(dataSel[1])

        self.inputData['input'].Bind(wx.EVT_TEXT, self.OnVectSel) # TODO optimization
        self.inputData['alayer'].Bind(wx.EVT_TEXT, self.OnALayerSel)
        self.inputData['nlayer'].Bind(wx.EVT_TEXT, self.OnNLayerSel)

        # Layout
        mainSizer = wx.BoxSizer(wx.VERTICAL)
        box = wx.StaticBox(dataPanel, -1, "Vector map and layers for analysis")
        bsizer = wx.StaticBoxSizer(box, wx.VERTICAL)

        mainSizer.Add(item = bsizer, proportion = 0,
                      flag = wx.EXPAND  | wx.TOP | wx.LEFT | wx.RIGHT, border = 5) 

        for sel in ['input', 'alayer', 'nlayer']:
            if sel== 'input' and self.mapWin.tree:
                btn = self.addToTreeBtn
            else:
                btn = None
            selPanels[sel].SetSizer(self._doSelLayout(title = label[sel], 
                                                      sel = self.inputData[sel], 
                                                      btn = btn))
            bsizer.Add(item = selPanels[sel], proportion = 1,
                       flag = wx.EXPAND)

        box = wx.StaticBox(dataPanel, -1, "Costs")    
        bsizer = wx.StaticBoxSizer(box, wx.VERTICAL)

        mainSizer.Add(item = bsizer, proportion = 0,
                                 flag = wx.EXPAND  | wx.TOP | wx.LEFT | wx.RIGHT, border = 5)       

        for sel in ['afcolumn', 'abcolumn', 'ncolumn']:
            selPanels[sel].SetSizer(self._doSelLayout(title = label[sel], sel = self.inputData[sel]))
            bsizer.Add(item = selPanels[sel], proportion = 0,
                       flag = wx.EXPAND)

        dataPanel.SetSizer(mainSizer)

    def _doSelLayout(self, title, sel, btn = None): 

        selSizer = wx.BoxSizer(orient = wx.VERTICAL)

        selTitleSizer = wx.BoxSizer(wx.HORIZONTAL)
        selTitleSizer.Add(item = title, proportion = 1,
                          flag = wx.LEFT | wx.TOP | wx.EXPAND, border = 5)

        selSizer.Add(item = selTitleSizer, proportion = 0,
                                 flag = wx.EXPAND)

        if btn:
                selFiledSizer = wx.BoxSizer(orient = wx.HORIZONTAL)
                selFiledSizer.Add(item = sel, proportion = 1,
                             flag = wx.EXPAND | wx.ALL)

                selFiledSizer.Add(item = btn, proportion = 0,
                             flag = wx.EXPAND | wx.ALL)

                selSizer.Add(item = selFiledSizer, proportion = 0,
                             flag = wx.EXPAND | wx.ALL| wx.ALIGN_CENTER_VERTICAL,
                             border = 5)
        else:
                selSizer.Add(item = sel, proportion = 1,
                             flag = wx.EXPAND | wx.ALL| wx.ALIGN_CENTER_VERTICAL,
                             border = 5)
        return selSizer

    def OnToTreeBtn(self, event):
        """!Adds vector map into layer tree (button next to map select)"""
        vectorMap = self.inputData['input'].GetValue()
        existsMap = grass.find_file(name = vectorMap, 
                                    element = 'vector', 
                                    mapset = grass.gisenv()['MAPSET'])
        if not existsMap["name"]:
            return

        cmd = ['d.vect', 
               'map=' + vectorMap]

        if self.mapWin.tree.FindItemByData(key = 'name', value = vectorMap) is None: 
            self.mapWin.tree.AddLayer(ltype = "vector", 
                                      lcmd = cmd,
                                      lname =vectorMap,
                                      lchecked = True)           

    def OnVectSel(self, event):
        """!When vector map is selected populates other selects (layer selects, columns selects)"""
        if self.snapping:
            self.OnSnapping(event = None)

        self.inputData['alayer'].Clear()
        self.inputData['nlayer'].Clear()

        self.inputData['alayer'].InsertLayers(vector = self.inputData['input'].GetValue().strip())
        self.inputData['nlayer'].InsertLayers(vector = self.inputData['input'].GetValue().strip())

        items = self.inputData['alayer'].GetItems()
        itemsLen = len(items)
        if itemsLen < 1:
            if self.mapWin.tree:
                self.addToTreeBtn.Disable()
            self.inputData['alayer'].SetValue("")
            self.inputData['nlayer'].SetValue("")
            for sel in ['afcolumn', 'abcolumn', 'ncolumn']:
                self.inputData[sel].Clear()
                self.inputData[sel].SetValue("")
            return
        elif itemsLen == 1:
            self.inputData['alayer'].SetSelection(0)
            self.inputData['nlayer'].SetSelection(0)
        elif itemsLen >= 1:
            if unicode("1") in items:
                iItem = items.index(unicode("1")) 
                self.inputData['alayer'].SetSelection(iItem)
            if unicode("2") in items:
                iItem = items.index(unicode("2")) 
                self.inputData['nlayer'].SetSelection(iItem)

        if self.mapWin.tree:
            self.addToTreeBtn.Enable()

        self.OnALayerSel(event) 
        self.OnNLayerSel(event)

    def OnALayerSel(self, event):
        """!When arc layer from vector map is selected, populates corespondent columns selects"""
        self.inputData['afcolumn'].InsertColumns(vector = self.inputData['input'].GetValue(), 
                                                 layer = self.inputData['alayer'].GetValue(), 
                                                 type = self.columnTypes)
        self.inputData['abcolumn'].InsertColumns(vector = self.inputData['input'].GetValue(), 
                                                 layer = self.inputData['alayer'].GetValue(), 
                                                 type = self.columnTypes)


    def OnNLayerSel(self, event):
        """!When node layer from vector map is selected, populates corespondent column select"""
        if self.snapping:
            self.OnSnapping(event = None)

        self.inputData['ncolumn'].InsertColumns(vector = self.inputData['input'].GetValue(), 
                                                layer = self.inputData['nlayer'].GetValue(), 
                                                type = self.columnTypes)
 
    def _getInvalidInputs(self, inpToTest):

        errInput = {}

        curr_mapset = grass.gisenv()['MAPSET']
        vectMaps = grass.list_grouped('vect')[curr_mapset]
        mapVal = self.inputData['input'].GetValue()
        mapVal = mapVal.split("@")[0]

        if not inpToTest or "input" in inpToTest:
            if mapVal not in vectMaps:
                errInput['input'] = mapVal

        for layerSelName in ['alayer', 'nlayer'] :
            if not inpToTest or layerSelName in inpToTest:

                layerItems = self.inputData[layerSelName].GetItems()
                layerVal = self.inputData[layerSelName].GetValue().strip()
                if layerVal not in layerItems:
                    errInput[layerSelName] = layerVal

        currModCols = self.vnetParams[self.currAnModule]["cmdParams"]["cols"]
        for col, colData in self.attrCols.iteritems():
            if not inpToTest or col in inpToTest:

                if col not in currModCols:
                    continue  

                if "inputField" in self.attrCols[col]: # TODO function??
                    colInptF = self.attrCols[col]["inputField"]
                else:
                    colInptF = col

                if not self.inputData[colInptF].IsShown():
                    continue
                colVal = self.inputData[colInptF].GetValue().strip()

                if not colVal:
                    continue
                if colVal not in self.inputData[colInptF].GetItems():
                    errInput[col] = colVal

        return errInput

    def InputsErrorMsgs(self, strToStart, inpToTest = None):

        errInput = self._getInvalidInputs(inpToTest)

        errMapStr = ""
        if errInput.has_key('input'):
            self.notebook.SetSelectionByName("parameters")
            if errInput['input']:
                errMapStr = _("Vector map '%s' does not exist.") %  (errInput['input'])
            else:
                errMapStr = _("Vector map was not chosen.")


        if errMapStr:
            GMessage(parent = self,
                     message = strToStart + "\n" + errMapStr)
            return False

        errLayerStr = ""
        for layer, layerLabel in {'alayer' : _("arc layer"), 
                                  'nlayer' : _("node layer")}.iteritems():

            if  errInput.has_key(layer):
                if errInput[layer]:
                    errLayerStr += _("Chosen %s '%s' does not exist in vector map '%s'.\n") % \
                                   (layerLabel, self.inputData[layer].GetValue(), self.inputData['input'].GetValue())
                else:
                    errLayerStr += _("Choose existing %s.\n") % \
                                   (layerLabel)
        if errLayerStr:
            GMessage(parent = self,
                     message = strToStart + "\n" + errLayerStr)
            return False

        errColStr = ""
        for col, colData in self.attrCols.iteritems():
            if col in errInput.iterkeys():
                errColStr += _("Chosen column '%s' does not exist in attribute table of layer '%s' of vector map '%s'.\n") % \
                             (errInput[col], self.inputData[layer].GetValue(), self.inputData['input'].GetValue())

        if errColStr:
            self.notebook.SetSelectionByName("parameters")                   
            GMessage(parent = self,
                     message =strToStart + "\n" + errColStr)
            return False

        return True

    def OnCloseDialog(self, event):
        """!Cancel dialog"""
        self.parent.dialogs['vnet'] = None
        self.Destroy()

    def SetPointStatus(self, item, itemIndex):
        """!Before point is drawn, decides properties of drawing style"""
        key = self.list.GetItemData(itemIndex)
        point = self.list.itemDataMap[key] #TODO public method in list?

        cats = self.vnetParams[self.currAnModule]["cmdParams"]["cats"]

        if key == self.list.selected:
            wxPen = "selected"
        elif not self.list.IsChecked(key):
                wxPen = "unused"
                item.hide = False
        elif len(cats) > 1:
            if point[1] == cats[1][1]:
                wxPen = "used2cat"
            else:
                wxPen = "used1cat"              
        else:
            wxPen = "used1cat"       

        item.SetPropertyVal('label', str(itemIndex + 1))
        item.SetPropertyVal('penName', wxPen)       

    def OnMapClickHandler(self, event):
        """!Takes coordinates from map window."""
        if event == 'unregistered':
            ptListToolbar = self.toolbars['pointsList']
            if ptListToolbar:
                ptListToolbar.ToggleTool( id = ptListToolbar.GetToolId("insertPoint"),
                                          toggle = False)  
            self.handlerRegistered = False
            return

        self.notebook.SetSelectionByName("points")
        if not self.list.itemDataMap:
            self.list.AddItem(None)

        e, n = self.mapWin.GetLastEN()

        index = self.list.selected
        key = self.list.GetItemData(index)

        if self.snapping:
            coords = [e, n]
            if self._snapPoint(coords):
                colNum = self.list.GetColumnNum('topology')
                self.list.EditCellKey(key = self.list.selected , 
                                      col = colNum, 
                                      cellData = _("snapped to node"))
            else:
                colNum = self.list.GetColumnNum('topology')
                self.list.EditCellKey(key = self.list.selected , 
                                    col = colNum, 
                                    cellData = _("new point"))

            e = coords[0]
            n = coords[1]

        else:
            colNum = self.list.GetColumnNum('topology')
            self.list.EditCellKey(key = self.list.selected , 
                                  col = colNum, 
                                  cellData = _("new point"))

        self.pointsToDraw.GetItem(key).SetCoords([e, n])

        if self.list.selected == self.list.GetItemCount() - 1:
            self.list.selected = 0
        else:
            self.list.selected += 1
        self.list.Select(self.list.selected)

        self.mapWin.UpdateMap(render=False, renderVector=False)

    def _snapPoint(self, coords):

        e = coords[0]
        n = coords[1]

        snapTreshPix = int(UserSettings.Get(group ='vnet', 
                                            key = 'other', 
                                            subkey = 'snap_tresh'))
        res = max(self.mapWin.Map.region['nsres'], self.mapWin.Map.region['ewres'])
        snapTreshDist = snapTreshPix * res

        inpMapExists = grass.find_file(name = self.inputData['input'].GetValue(), 
                                       element = 'vector', 
                                       mapset = grass.gisenv()['MAPSET'])
        if not inpMapExists['name']:
            return False

        openedMap = pointer(vectlib.Map_info())
        ret = vectlib.Vect_open_old2(openedMap, 
                                     c_char_p(self.inputData['input'].GetValue()),
                                     c_char_p(grass.gisenv()['MAPSET']),
                                     c_char_p(self.inputData['alayer'].GetValue()))
        if ret == 1:
            vectlib.Vect_close(openedMap)
        if ret != 2: 
            return False

        nodeNum =  vectlib.Vect_find_node(openedMap,     
                                          c_double(e), 
                                          c_double(n), 
                                          c_double(0), 
                                          c_double(snapTreshDist),
                                          vectlib.WITHOUT_Z)

        if nodeNum > 0:
            e = c_double(0)
            n = c_double(0)
            vectlib.Vect_get_node_coor(openedMap, 
                                       nodeNum, 
                                       byref(e), 
                                       byref(n), 
                                       None); # z
            e = e.value
            n = n.value
        else:
            vectlib.Vect_close(openedMap)
            return False

        coords[0] = e
        coords[1] = n
        return True

    def OnAnalyze(self, event):
        """!Called when network analysis is started"""
        # Check of parameters for analysis
        if not self.InputsErrorMsgs(strToStart = _("Analysis can not be done.")):
            return

        if self.tmp_result:
            self.tmp_result.DeleteRenderLayer()

        self.tmpVectMapsToHist= []#TODO 
        self.tmp_result = self.NewTmpVectMapToHist('vnet_tmp_result')
        if not self.tmp_result:
                return          
        elif not self.CheckAnMapState(self.tmp_result):
                return 
        self._saveAnInputToHist()

        # Creates part of cmd fro analysis
        cmdParams = [self.currAnModule]
        cmdParams.extend(self._getInputParams())
        cmdParams.append("output=" + self.tmp_result.GetVectMapName())

        catPts = self._getPtByCat()

        if self.currAnModule == "v.net.path":
            self._vnetPathRunAn(cmdParams, catPts)
        else:
            self._runAn(cmdParams, catPts)

    def _vnetPathRunAn(self, cmdParams, catPts):
        """!Called when analysis is run for v.net.path module"""
        if len(self.pointsToDraw.GetAllItems()) < 1:
            return

        catPts = self._getPtByCat()
        cats = self.vnetParams[self.currAnModule]["cmdParams"]["cats"]

        cmdPts = []
        for cat in cats:
            if  len(catPts[cat[0]]) < 1:
                GMessage(parent = self,
                         message=_("Pleas choose 'to' and 'from' point."))
                return
            cmdPts.append(catPts[cat[0]][0])


        resId = 1 #int(UserSettings.Get(group ='vnet', 
                  #                     key = 'analysis_settings', 
                  #                     subkey = 'resultId'))

        inpPoints = str(resId) + " " + str(cmdPts[0][0]) + " " + str(cmdPts[0][1]) + \
                                 " " + str(cmdPts[1][0]) + " " + str(cmdPts[1][1])

        self.coordsTmpFile = grass.tempfile()
        coordsTmpFileOpened = open(self.coordsTmpFile, 'w')
        coordsTmpFileOpened.write(inpPoints)
        coordsTmpFileOpened.close()

        cmdParams.append("file=" + self.coordsTmpFile)

        #dmax = int(UserSettings.Get(group = 'vnet', 
        #                            key ='analysis_settings', 
        #                            subkey ='maxDist'))

        cmdParams.append("dmax=" + str(self.anSettings["max_dist"].GetValue()))
        cmdParams.append("input=" + self.inputData['input'].GetValue())

        cmdParams.append("--overwrite")
        self._prepareCmd(cmd = cmdParams)

        self.goutput.RunCmd(command = cmdParams, onDone = self._vnetPathRunAnDone)

    def _vnetPathRunAnDone(self, cmd, returncode):
        """!Called when v.net.path analysis is done"""
        grass.try_remove(self.coordsTmpFile)

        self._saveHistStep()

        self.tmp_result.SaveVectMapState()

        cmd = self.GetLayerStyle()
        self.tmp_result.AddRenderLayer(cmd)
        self.mapWin.UpdateMap(render=True, renderVector=True)

    def _runAn(self, cmdParams, catPts):
        """!Called for all v.net.* analysis (except v.net.path)"""
        # TODO how to get output in ondone function
        #cmdCategory = [ "v.category",
        #                "input=" + self.inputData['input'].GetValue(),
        #                "option=report",
        #                "-g",
        #              ]
        cats = RunCommand("v.category",
                           input = self.inputData['input'].GetValue(),
                           option = "report",
                           flags = "g",
                           read = True)     

        cats = cats.splitlines()
        for cat in cats:#TODO
            cat = cat.split()
            if "all" in cat:
                maxCat = int(cat[4])
                break

        layerNum = self.inputData["nlayer"].GetValue().strip()

        pt_ascii, catsNums = self._getAsciiPts (catPts = catPts, 
                                                maxCat = maxCat, 
                                                layerNum = layerNum)

        self.tmpPtsAsciiFile = grass.tempfile()#TODO tmp files cleanup
        tmpPtsAsciiFileOpened = open(self.tmpPtsAsciiFile, 'w')
        tmpPtsAsciiFileOpened.write(pt_ascii)
        tmpPtsAsciiFileOpened.close()

        self.tmpInPts = self._addTmpMapAnalysisMsg("vnet_tmp_in_pts")
        if not self.tmpInPts:
            return

        self.tmpInPtsConnected = self._addTmpMapAnalysisMsg("vnet_tmp_in_pts_connected")
        if not self.tmpInPtsConnected:
            return
        #dmax = int(UserSettings.Get(group = 'vnet', 
        #                            key ='analysis_settings', 
        #                            subkey ='maxDist'))

        cmdParams.append("input=" + self.tmpInPtsConnected.GetVectMapName())
        cmdParams.append("--overwrite")  

        if self.currAnModule == "v.net.distance":
            cmdParams.append("from_layer=1")
            cmdParams.append("to_layer=1")
        elif self.currAnModule == "v.net.flow":
            self.vnetFlowTmpCut = self.NewTmpVectMapToHist('vnet_tmp_flow_cut')
            if not self.vnetFlowTmpCut:
                return          
            elif not self.CheckAnMapState(self.vnetFlowTmpCut):
                return 
            cmdParams.append("cut=" +  self.vnetFlowTmpCut.GetVectMapName())         
        elif self.currAnModule == "v.net.iso":
            costs = self.anSettings["iso_lines"].GetValue()
            cmdParams.append("costs=" + costs)          
        for catName, catNum in catsNums.iteritems():
            if catNum[0] == catNum[1]:
                cmdParams.append(catName + "=" + str(catNum[0]))
            else:
                cmdParams.append(catName + "=" + str(catNum[0]) + "-" + str(catNum[1]))

        cmdVEdit = [ 
                    "v.edit",
                    "map=" + self.tmpInPts.GetVectMapName(), 
                    "input=" + self.tmpPtsAsciiFile,
                    "tool=create",
                    "--overwrite", 
                    "-n"                              
                   ]
        self._prepareCmd(cmdVEdit)
        self.goutput.RunCmd(command = cmdVEdit)

        cmdVNet = [
                    "v.net",
                    "points=" + self.tmpInPts.GetVectMapName(), 
                    "input=" + self.inputData["input"].GetValue(),
                    "output=" + self.tmpInPtsConnected.GetVectMapName(),
                    "alayer=" +  self.inputData["alayer"].GetValue().strip(),
                    "nlayer=" +  self.inputData["nlayer"].GetValue().strip(), 
                    "operation=connect",
                    "thresh=" + str(self.anSettings["max_dist"].GetValue()),             
                    "--overwrite"                         
                  ]
        self._prepareCmd(cmdVNet)
        self.goutput.RunCmd(command = cmdVNet)

        self._prepareCmd(cmdParams)
        self.goutput.RunCmd(command = cmdParams, onDone = self._runAnDone)

    def _runAnDone(self, cmd, returncode):
        """!Called when analysis is done"""
        self.tmpMaps.DeleteTmpMap(self.tmpInPts) #TODO remove earlier (ondone lambda?)
        self.tmpMaps.DeleteTmpMap(self.tmpInPtsConnected)
        grass.try_remove(self.tmpPtsAsciiFile)

        self._saveHistStep()

        self.tmp_result.SaveVectMapState()
        cmd = self.GetLayerStyle()
        self.tmp_result.AddRenderLayer(cmd)
        self.mapWin.UpdateMap(render=True, renderVector=True)

    def _getInputParams(self):
        inParams = []
        for col in self.vnetParams[self.currAnModule]["cmdParams"]["cols"]:

            if "inputField" in self.attrCols[col]:
                colInptF = self.attrCols[col]["inputField"]
            else:
                colInptF = col

            inParams.append(col + '=' + self.inputData[colInptF].GetValue())

        for layer in ['alayer', 'nlayer']:
            inParams.append(layer + "=" + self.inputData[layer].GetValue().strip())

        return inParams

    def _getPtByCat(self):
        """!Returns points separated by theirs categories"""
        cats = self.vnetParams[self.currAnModule]["cmdParams"]["cats"]

        ptByCats = {}
        for cat in self.vnetParams[self.currAnModule]["cmdParams"]["cats"]:
            ptByCats[cat[0]] = []
 
        for i in range(len(self.list.itemDataMap)):
            key = self.list.GetItemData(i)
            if self.list.IsChecked(key):
                for cat in cats:
                    if cat[1] == self.list.itemDataMap[key][1] or len(ptByCats) == 1: 
                        ptByCats[cat[0]].append(self.pointsToDraw.GetItem(key).GetCoords())
                        continue

        return ptByCats

    def _getAsciiPts (self, catPts, maxCat, layerNum):
        """!Returns points separated by categories in GRASS ASCII vector representation"""
        catsNums = {}
        pt_ascii = ""
        catNum = maxCat

        for catName, pts in catPts.iteritems():

            catsNums[catName] = [catNum + 1]
            for pt in pts:
                catNum += 1
                pt_ascii += "P 1 1\n"
                pt_ascii += str(pt[0]) + " " + str(pt[1]) +  "\n"
                pt_ascii += str(layerNum) + " " + str(catNum) + "\n"

            catsNums[catName].append(catNum)

        return pt_ascii, catsNums

    def _prepareCmd(self, cmd):
        """!Helper function for preparation of cmd in list into form for RunCmd method"""
        for c in cmd[:]:
            if c.find("=") == -1:
                continue
            v = c.split("=")
            if len(v) != 2:
                cmd.remove(c)
            elif not v[1].strip():
                cmd.remove(c)

    def CheckAnMapState(self, vectMap):

        vectMapState = vectMap.VectMapState()

        if vectMapState == 0:
            dlg = wx.MessageDialog(parent = self,
                                   message = _("Map %s was changed outside " +
                                                "of vector network analysis tool. " +
                                                "Do you want to continue in analysis and " +
                                                "overwrite it?") % (self.vectMap.GetVectMapName()),
                                   caption = _("Overwrite map"),
                                   style = wx.YES_NO | wx.NO_DEFAULT |
                                           wx.ICON_QUESTION | wx.CENTRE)            
            ret = dlg.ShowModal()
            dlg.Destroy()
            
            if ret == wx.ID_NO:
                self.tmpMaps.RemoveFromTmpMaps(vectMap)
                return False
            
        return True

    def GetLayerStyle(self):
        """!Returns cmd for d.vect, with set style for analysis result"""
        resStyle = self.vnetParams[self.currAnModule]["resultStyle"]

        width = UserSettings.Get(group='vnet', key='res_style', subkey= "line_width")
        layerStyleCmd = ["layer=1",'width=' + str(width)]

        if "catColor" in resStyle:
            layerStyleCmd.append('flags=c')
        elif "singleColor" in resStyle:
            col = UserSettings.Get(group='vnet', key='res_style', subkey= "line_color")
            layerStyleCmd.append('color=' + str(col[0]) + ':' + str(col[1]) + ':' + str(col[2]))        

        if "attrColColor" in resStyle:
            self.layerStyleVnetColors = [
                                          "v.colors",
                                          "map=" + self.tmp_result.GetVectMapName(),
                                          "color=byr",#TODO
                                          "column=" + resStyle["attrColColor"],
                                        ]
            self.layerStyleVnetColors  = utils.CmdToTuple(self.layerStyleVnetColors)

            RunCommand( self.layerStyleVnetColors[0],
                        **self.layerStyleVnetColors[1])

        return layerStyleCmd 

    def OnShowResult(self, event):
        """!Shows, hides analysis result - not yet implemented"""
        mainToolbar = self.toolbars['mainToolbar']
        id = vars(mainToolbar)['showResult'] #TODO
        toggleState = mainToolbar.GetToolState(id)

        if toggleState:
            cmd = self.GetLayerStyle()
            self.tmp_result.AddRenderLayer(cmd)
        else:
            cmd = self.GetLayerStyle()
            self.tmp_result.DeleteRenderLayer(cmd)

        self.mapWin.UpdateMap(render=True, renderVector=True)

    def OnInsertPoint(self, event):
        """!Registers/unregisters mouse handler into map window"""
        if self.handlerRegistered == False:
            self.mapWin.RegisterMouseEventHandler(wx.EVT_LEFT_DOWN, 
                                                  self.OnMapClickHandler,
                                                  wx.StockCursor(wx.CURSOR_CROSS))
            self.handlerRegistered = True
        else:
            self.mapWin.UnregisterMouseEventHandler(wx.EVT_LEFT_DOWN, 
                                                  self.OnMapClickHandler)
            self.handlerRegistered = False

    def OnSaveTmpLayer(self, event):
        """!Permanently saves temporary map of analysis result"""
        dlg = AddLayerDialog(parent = self)#TODO import location check?

        msg = _("Vector map with analysis result does not exist.")
        if dlg.ShowModal() == wx.ID_OK:

            if not hasattr(self.tmp_result, "GetVectMapName"):
                GMessage(parent = self,
                         message = msg)
                return

            mapToAdd = self.tmp_result.GetVectMapName()
            mapToAddEx = grass.find_file(name = mapToAdd, 
                                        element = 'vector', 
                                        mapset = grass.gisenv()['MAPSET'])

            if not mapToAddEx["name"]: 
                GMessage(parent = self,
                         message = msg)
                return

            addedMap = dlg.vectSel.GetValue()
            existsMap = grass.find_file(name = addedMap, 
                                        element = 'vector', 
                                        mapset = grass.gisenv()['MAPSET'])

            if existsMap["name"]:
                dlg = wx.MessageDialog(parent = self.parent.parent,
                                       message = _("Vector map %s already exists. " +
                                                "Do you want to overwrite it?") % 
                                                (existsMap["fullname"]),
                                       caption = _("Overwrite map layer"),
                                       style = wx.YES_NO | wx.NO_DEFAULT |
                                               wx.ICON_QUESTION | wx.CENTRE)            
                ret = dlg.ShowModal()
                if ret == wx.ID_NO:
                    return

            RunCommand("g.copy",
                       overwrite = True,
                       vect = [self.tmp_result.GetVectMapName(), addedMap])

            cmd = self.GetLayerStyle()#TODO get rid of insert
            cmd.insert(0, 'd.vect')
            cmd.append('map=%s' % addedMap)

            if not self.mapWin.tree:
                return

            if  self.mapWin.tree.FindItemByData(key = 'name', value = addedMap) is None: 
                self.mapWin.tree.AddLayer(ltype = "vector", 
                                          lname = addedMap,
                                          lcmd = cmd,
                                          lchecked = True)

            #self.mapWin.UpdateMap(render=True, renderVector=True) 

    def OnSettings(self, event):
        """!Displays vnet settings dialog"""
        dlg = SettingsDialog(parent=self, id=wx.ID_ANY, title=_('Settings'))
        
        if dlg.ShowModal() == wx.ID_OK:
            pass
        
        dlg.Destroy()

    def OnAnalysisChanged(self, event):
        """!Updates dialog when analysis is changed"""
        # finds module name according to value in anChoice
        for module, params in self.vnetParams.iteritems():
            chLabel = self.toolbars['mainToolbar'].anChoice.GetValue()
            if params["label"] == chLabel:
                self.currAnModule = module
                break

        if self.currAnModule == "v.net.path":
            self.list._updateCheckedItems(index = -1)
        #    self.anSettings['line_id'].GetParent().Show()
        #else:
        #    self.anSettings['line_id'].GetParent().Hide()

        if self.currAnModule == "v.net.iso":
            self.anSettings['iso_lines'].GetParent().Show()
        else:
            self.anSettings['iso_lines'].GetParent().Hide()

        #if self.currAnModule == "v.net.flow":
        #    self.anSettings['show_cut'].GetParent().Show()
        #else:
        #    self.anSettings['show_cut'].GetParent().Hide()

        # Show only corresponding selects for chosen v.net module
        skip = []
        for col in self.attrCols.iterkeys():
            if "inputField" in self.attrCols[col]:
                colInptF = self.attrCols[col]["inputField"]
            else:
                colInptF = col

            if col in skip:
                continue

            inputPanel = self.inputData[colInptF].GetParent()
            if col in self.vnetParams[self.currAnModule]["cmdParams"]["cols"]:
                inputPanel.Show()
                inputPanel.FindWindowByName(colInptF).SetLabel(self.attrCols[col]["label"])
                inputPanel.Layout()
                if col != colInptF:
                    skip.append(colInptF)
            else:
                self.inputData[colInptF].GetParent().Hide()
        self.Layout()

        # If module has only one category -> hide type column in points list otherwise show it
        if len(self.vnetParams[self.currAnModule]["cmdParams"]["cats"]) > 1:
            if self.list.GetColumnNum('type') == -1:
                self.list.ShowColumn('type', 1)

            prevParamsCats = self.vnetParams[self.prev2catsAnModule]["cmdParams"]["cats"]
            currParamsCats = self.vnetParams[self.currAnModule]["cmdParams"]["cats"]

            self.list._adaptPointsList(currParamsCats, prevParamsCats)
            self.prev2catsAnModule = self.currAnModule
        else:
            if self.list.GetColumnNum('type') != -1:
                self.list.HideColumn('type')

    def OnSnapping(self, event):

        ptListToolbar = self.toolbars['pointsList']
        if not haveCtypes:
            ptListToolbar.ToggleTool(id = ptListToolbar.GetToolId("snapping"),
                                     toggle = False)
            GMessage(parent = self,
                     message = _("Unable to use ctypes. \n") + \
                               _("Snapping mode can not be activated."))
            return

        if not event or not event.IsChecked():
            if not event: 
                ptListToolbar.ToggleTool(id = ptListToolbar.GetToolId("snapping"),
                                         toggle = False)
            if self.tmpMaps.HasTmpVectMap("vnet_snap_points"):
                self.snapPts.DeleteRenderLayer() 
                self.mapWin.UpdateMap(render = False, renderVector = False)
            self.snapping = False
            return  

        if not self.InputsErrorMsgs(strToStart = _("Snapping mode can not be activated."),
                                    inpToTest = ["input", "nlayer"]):

            ptListToolbar.ToggleTool(id = ptListToolbar.GetToolId("snapping"),
                                     toggle = False)
            return

        if not self.tmpMaps.HasTmpVectMap("vnet_snap_points"):
            endStr = _("Do you really want to activate snapping and overwrite it?")
            self.snapPts = self.tmpMaps.AddTmpVectMap("vnet_snap_points", endStr)
            if not self.snapPts:
                ptListToolbar.ToggleTool(id = ptListToolbar.GetToolId("snapping"),
                                         toggle = False)
                return       
        elif self.snapPts.VectMapState() == 0:
                dlg = wx.MessageDialog(parent = self.parent,
                                       message = _("Temporary map '%s' was changed outside " +
                                                    "vector analysis tool.\n" 
                                                    "Do you really want to activate " + 
                                                    "snapping and overwrite it? ") % \
                                                    self.snapPts.GetVectMapName(),
                                        caption = _("Overwrite map"),
                                        style = wx.YES_NO | wx.NO_DEFAULT |
                                                wx.ICON_QUESTION | wx.CENTRE)

                ret = dlg.ShowModal()
                dlg.Destroy()
                
                if ret == wx.ID_NO:
                    self.tmpMaps.DeleteTmpMap(self.snapPts)
                    ptListToolbar.ToggleTool(id = ptListToolbar.GetToolId("snapping"),
                                             toggle = False)
                    return

        self.snapping = True

        currMapSet = grass.gisenv()['MAPSET'] 
        inpName = self.inputData["input"].GetValue()
        inpFullName = inpName + "@" + currMapSet

        computeNodes = True

        if not self.snapData:
            pass
        elif inpFullName != self.snapData["inputMap"].GetVectMapName():
            self.snapData["inputMap"] = VectMap(self, inpFullName)
        elif self.snapData["inputMapNlayer"] == self.inputData["nlayer"].GetValue():
            if self.snapData["inputMap"].VectMapState() == 1:
                computeNodes = False
    
        if computeNodes:
            self.cmdThread = CmdThread(self)

            cmd = ["v.to.points", "input=" + self.inputData["input"].GetValue(), 
                                  "output=" + self.snapPts.GetVectMapName(),
                                  "llayer=" + self.inputData["nlayer"].GetValue(),
                                  "-n", "--overwrite"]
            # process GRASS command with argument
            self.Bind(EVT_CMD_DONE, self._onToPointsDone)
            self.cmdThread.RunCmd(cmd)

            self.snapData["inputMap"] = VectMap(self, inpFullName)
            self.snapData["inputMapNlayer"] = self.inputData["nlayer"].GetValue()
        else:
            self.snapPts.AddRenderLayer()           
            self.mapWin.UpdateMap(render = True, renderVector = True)
        

    def _onToPointsDone(self, event):

        self.snapPts.SaveVectMapState()
        self.snapPts.AddRenderLayer() 
        self.mapWin.UpdateMap(render = True, renderVector = True)

    def OnUndo(self, event):
        histStepData = self.history.GetPrev()
        self.toolbars['mainToolbar'].UpdateUndoRedo()

        if histStepData:
            self._updateHistStepData(histStepData)

    def OnRedo(self, event):
        histStepData = self.history.GetNext()
        self.toolbars['mainToolbar'].UpdateUndoRedo()

        if histStepData:
            self._updateHistStepData(histStepData)

    def _saveAnInputToHist(self):

        pts = self.pointsToDraw.GetAllItems()

        for iPt, pt in enumerate(pts):
            ptName = "pt" + str(iPt)

            coords = pt.GetCoords()
            self.history.Add(key = "points", 
                             subkey = [ptName, "coords"], 
                             value = coords)

            colNum = self.list.GetColumnNum('type')
            if colNum != -1:
                cat = self.list.GetCellText(iPt, 1)#TODO
                self.history.Add(key = "points", 
                                 subkey = [ptName, "cat"], 
                                 value = cat)

            topology = self.list.GetCellText(iPt, 2)
            self.history.Add(key = "points", 
                             subkey = [ptName, "topology"], 
                             value = topology)


            self.history.Add(key = "points", 
                             subkey = [ptName, "checked"], 
                             value = self.list.IsChecked(iPt))

            for inpName, inp in self.inputData.iteritems():
                if inpName == "input":
                    currMapSet = grass.gisenv()['MAPSET'] 
                    inpMap = VectMap(self, inp.GetValue() + "@" + currMapSet)
                    self.history.Add(key = "other", 
                                     subkey = "input_modified", 
                                     value = inpMap.GetLastModified())                    
                self.history.Add(key = "input_data", 
                                 subkey = inpName, 
                                 value = inp.GetValue())
            #else:
            #    self.history.Add(key = "points", 
            #                     subkey = [ptName, "prev2catsAnModule"], 
            #                     value = self.prev2catsAnModule)

            
        self.history.Add(key = "vnet_modules", subkey = "curr_module", value = self.currAnModule)

    def _saveHistStep(self):

        removedHistData = self.history.SaveHistStep()
        self.toolbars['mainToolbar'].UpdateUndoRedo()

        if not removedHistData:
            return

        for removedStep in removedHistData.itervalues():
            mapsNames = removedStep["tmp_data"]["maps"]
            for vectMapName in mapsNames:
                tmpMap = self.tmpMaps.GetTmpVectMap(vectMapName)
                self.tmpMaps.DeleteTmpMap(tmpMap)

    def _updateHistStepData(self, histStepData):#TODO need optimization

        self.currAnModule = histStepData["vnet_modules"]["curr_module"]
        anChoice = self.toolbars['mainToolbar'].anChoice
        anChoice.SetStringSelection(self.vnetParams[self.currAnModule]["label"]) #TODO

        self.list.SetUpdateMap(updateMap = False)
        while self.list.GetSelected() != wx.NOT_FOUND:
            self.list.DeleteItem()

        for iPt in range(len(histStepData["points"])):

            ptData = histStepData["points"]["pt" + str(iPt)]
            coords = ptData["coords"]
            self.list.AddItem()
            item = self.pointsToDraw.GetItem(iPt)
            item.SetCoords(coords)

            if ptData.has_key('cat'):
                self.list.ShowColumn('type', 1)
                colNum = self.list.GetColumnNum('type')
                self.list.EditCellKey(iPt, colNum, ptData["cat"])
            else:
                self.list.HideColumn('type')

            topologyNum = self.list.GetColumnNum('topology')
            self.list.EditCellKey(iPt, topologyNum, ptData["topology"])           

            if ptData["checked"]:
                self.list.CheckItem(iPt, True)

        mapsNames = histStepData["tmp_data"]["maps"]
        for vectMapName in mapsNames:
            if "vnet_tmp_result" in vectMapName:
                self.tmp_result.DeleteRenderLayer()
                self.tmp_result  = self.tmpMaps.GetTmpVectMap(vectMapName)
                if self.tmp_result.VectMapState() == 0:#TODO test only 0
                    dlg = wx.MessageDialog(parent = self,
                                           message = _("Temporary map '%s' with result " + 
                                                       "was changed outside vector network analysis tool.\n" +
                                                       "Showed result may not correspond " +
                                                       "original analysis result.") %\
                                                        self.tmp_result.GetVectMapName(),
                                            caption = _("Result changed outside"),
                                            style =  wx.ICON_INFORMATION| wx.CENTRE)
                    dlg.ShowModal()
                    dlg.Destroy()

                cmd = self.GetLayerStyle()
                self.tmp_result.AddRenderLayer(cmd)

        histInputData = histStepData["input_data"]

        for inpName, inp in histInputData.iteritems():
            self.inputData[inpName].SetValue(str(inp)) #TODO order?
            if inpName == "input":
                inpMap = inp

        prevInpModTime = histStepData["other"]["input_modified"]

        inpMapFullName = inpMap + "@" + grass.gisenv()['MAPSET'] #TODO mapset changed?
        currInpModTime = VectMap(self, inpMapFullName).GetLastModified()

        if currInpModTime != prevInpModTime:
            dlg = wx.MessageDialog(parent = self,
                                   message = _("Input map '%s' for analysis was changed outside " + 
                                               "vector network analysis tool.\n" +
                                               "Topology column may not " +
                                               "correspond to changed situation.") %\
                                                inpMap,
                                   caption = _("Input changed outside"),
                                   style =  wx.ICON_INFORMATION| wx.CENTRE)
            dlg.ShowModal()
            dlg.Destroy()

        self.list.SetUpdateMap(updateMap = True)
        self.mapWin.UpdateMap(render=True, renderVector=True)

    def NewTmpVectMapToHist(self, prefMapName):

        mapName = prefMapName + str(self.histTmpVectMapNum)
        self.histTmpVectMapNum += 1
        tmpMap = self._addTmpMapAnalysisMsg(mapName)
        if not tmpMap:
            return tmpMap
           
        self.tmpVectMapsToHist.append(tmpMap.GetVectMapName())
        self.history.Add(key = "tmp_data", 
                         subkey = "maps",
                         value = self.tmpVectMapsToHist)

        return tmpMap

    def _addTmpMapAnalysisMsg(self, mapName):

        endStr = _("Do you want to continue in analysis and overwrite it?")
        tmpMap = self.tmpMaps.AddTmpVectMap(mapName, endStr)
        return tmpMap


    def _initVnetParams(self):
        """!Initializes parameters for different v.net.* modules """

        self.attrCols = {
                          'afcolumn' : {
                                        "label" : _("Arc forward/both direction(s) cost column:"),
                                        "name" : _("arc forward/both")
                                       },
                          'abcolumn' : {
                                        "label" : _("Arc backward direction cost column:"),
                                        "name" : _("arc backward")
                                       },
                          'acolumn' : {
                                       "label" : _("Arcs' cost column (for both directions):"),
                                       "name" : _("arc"),
                                       "inputField" : 'afcolumn',
                                      },
                          'ncolumn' : {
                                       "label" : _("Node cost column:"),
                                        "name" : _("node")                                      
                                      }
                        }

        self.vnetParams = {
                                   "v.net.path" : {
                                                     "label" : _("Shortest path %s") % "(v.net.path)",  
                                                     "cmdParams" : {
                                                                      "cats" :  [
                                                                                    ["st_pt", _("Start point")], 
                                                                                    ["end_pt", _("End point")] 
                                                                                ],
                                                                      "cols" :  [
                                                                                 'afcolumn',
                                                                                 'abcolumn',
                                                                                 'ncolumn'
                                                                                ],
                                                                   },
                                                     "resultStyle" : {"singleColor" : None}
                                                  },

                                    "v.net.salesman" : {
                                                        "label" : _("Salesman %s") % "(v.net.salesman)",  
                                                        "cmdParams" : {
                                                                        "cats" : [["ccats", None]],
                                                                        "cols" : [
                                                                                  'afcolumn',
                                                                                  'abcolumn'
                                                                                 ],
                                                                      },
                                                        "resultStyle" : {"singleColor" : None}
                                                       },
                                    "v.net.flow" : {
                                                     "label" : _("Flow %s") % "(v.net.flow)",  
                                                     "cmdParams" : {
                                                                      "cats" : [
                                                                                ["source_cats", _("Source point")], 
                                                                                ["sink_cats", _("Sink point")]
                                                                               ],                                                   
                                                                      "cols" : [
                                                                                'afcolumn',
                                                                                'abcolumn',
                                                                                'ncolumn'
                                                                               ]
                                                                  },
                                                     "resultStyle" : {"attrColColor": "flow"}
                                                   },
                                    "v.net.alloc" : {
                                                     "label" : _("Allocate subnets for nearest centers %s") % "(v.net.alloc)",  
                                                     "cmdParams" : {
                                                                      "cats" : [["ccats", None]],                           
                                                                      "cols" : [
                                                                                 'afcolumn',
                                                                                 'abcolumn',
                                                                                 'ncolumn'
                                                                               ]
                                                                  },
                                                     "resultStyle" :  {"catColor" : None }
                                                   },
                                    "v.net.steiner" : {
                                                     "label" : _("Create Steiner tree for the network and given terminals %s") % "(v.net.steiner)",  
                                                     "cmdParams" : {
                                                                      "cats" : [["tcats", None]],                           
                                                                      "cols" : [
                                                                                 'acolumn',
                                                                               ]
                                                                  },
                                                     "resultStyle" : {"singleColor" : None}
                                                   },
                                   "v.net.distance" : {
                                                       "label" : _("Computes shortest distance via the network %s") % "(v.net.distance)",  
                                                       "cmdParams" : {
                                                                        "cats" : [
                                                                                  ["from_cats", "From point"],
                                                                                  ["to_cats", "To point"]
                                                                                 ],
                                                                        "cols" : [
                                                                                  'afcolumn',
                                                                                  'abcolumn',
                                                                                  'ncolumn'
                                                                                 ],
                                                                  },
                                                      "resultStyle" : {"catColor" : None }
                                                     },
                                    "v.net.iso" :  {
                                                     "label" : _("Splits net by cost isolines %s") % "(v.net.iso)",  
                                                     "cmdParams" : {
                                                                      "cats" : [["ccats", None]],                           
                                                                      "cols" : [
                                                                                 'afcolumn',
                                                                                 'abcolumn',
                                                                                 'ncolumn'
                                                                               ]
                                                                  },
                                                     "resultStyle" : {"catColor" : None }
                                                   }
                                }

        self.vnetModulesOrder = ["v.net.path", 
                                 "v.net.salesman",
                                 "v.net.flow",
                                 "v.net.alloc",
                                 "v.net.distance",
                                 "v.net.iso",
                                 "v.net.steiner"
                                 ] # order in the choice of analysis
        self.currAnModule = self.vnetModulesOrder[0]
        self.prev2catsAnModule = self.vnetModulesOrder[0]

    def _initSettings(self):
        """!Initialization of settings (if not already defined)"""
        #if 'vnet' in UserSettings.userSettings:
        #   return

        # initializes default settings
        initSettings = [
                        ['res_style', 'line_width', 5],
                        ['res_style', 'line_color', (192,0,0)],
                        ['point_symbol', 'point_size', 10],             
                        ['point_symbol', 'point_width', 2],
                        ['point_colors', "unused", (131,139,139)],
                        ['point_colors', "used1cat", (192,0,0)],
                        ['point_colors', "used2cat", (0,0,255)],
                        ['point_colors', "selected", (9,249,17)],
                        ['other', "snap_tresh", 10],
                        ['other', "max_hist_steps", 5]
                       ]

        for init in initSettings: #TODO initialization warnings, all types are strs
            try:
                val = UserSettings.Get(group ='vnet',
                                       key = init[0],
                                       subkey =init[1])
                if type(val) != type(init[2]):
                    raise ValueError()

            except (KeyError, ValueError): 
       
                UserSettings.Append(dict = UserSettings.userSettings, 
                                    group ='vnet',
                                    key = init[0],
                                    subkey =init[1],
                                    value = init[2])


    def SetPointDrawSettings(self):
        """!Sets settings for drawing of points.
        """
        ptSize = int(UserSettings.Get(group='vnet', key='point_symbol', subkey = 'point_size'))
        self.pointsToDraw.SetPropertyVal("size", ptSize)

        colors = UserSettings.Get(group='vnet', key='point_colors')
        ptWidth = int(UserSettings.Get(group='vnet', key='point_symbol', subkey = 'point_width'))

        textProp = self.pointsToDraw.GetPropertyVal("text")
        textProp["font"].SetPointSize(ptSize + 2)
    
        for colKey, col in colors.iteritems():
            pen = self.pointsToDraw.GetPen(colKey)
            if pen:
                pen.SetColour(wx.Colour(col[0], col[1], col[2], 255))
                pen.SetWidth(ptWidth)
            else:
                self.pointsToDraw.AddPen(colKey, wx.Pen(colour = wx.Colour(col[0], col[1], col[2], 255), width = ptWidth))

class PtsList(PointsList):
    def __init__(self, parent, dialog, cols, id=wx.ID_ANY):
        """! List with points for analysis
        """
        self.updateMap = True
        self.dialog = dialog # VNETDialog class

        PointsList.__init__(self, parent = parent, cols = cols, id =  id)      

    def AddItem(self, event = None, updateMap = True):
        """!
        Appends point to list
        """
        self.dialog.pointsToDraw.AddItem(coords = [0,0])

        PointsList.AddItem(self, event)

        colNum = self.GetColumnNum('topology')
        self.EditCellKey(key = self.selected , 
                         col = colNum, 
                         cellData = _("new point"))  
 
    def DeleteItem(self, event = None):
        """!
        Deletes selected point in list
        """
        key = self.GetItemData(self.selected)
        if self.selected != wx.NOT_FOUND:
            item = self.dialog.pointsToDraw.GetItem(key)
            self.dialog.pointsToDraw.DeleteItem(item)

        PointsList.DeleteItem(self, event)

    def OnItemSelected(self, event):
        """
        Item selected
        """

        PointsList.OnItemSelected(self, event)

        if self.updateMap:
            self.dialog.mapWin.UpdateMap(render=False, renderVector=False)

    def _adaptPointsList(self, currParamsCats, prevParamsCats):
        """Rename category values when module is changed. Expample: Start point -> Sink point"""
        for item in enumerate(self.itemDataMap):            
            iCat = 0
            for ptCat in prevParamsCats:
                if self.itemDataMap[item[0]][1] ==  ptCat[1]:
                    colNum = self.GetColumnNum('type')
                    self.EditCellKey(item[0], colNum, currParamsCats[iCat][1])
                iCat += 1
            if not item[1][1]:               
                self.CheckItem(item[0], False)

        colValues = [""]
        for ptCat in currParamsCats:
            colValues.append(ptCat[1])

        self.ChangeColType(1, colValues)
    
    def OnCheckItem(self, index, flag):
        """!Item is checked/unchecked"""

        key = self.GetItemData(index)
        checkedVal = self.itemDataMap[key][1]

        currModule = self.dialog.currAnModule #TODO public func
        cats = self.dialog.vnetParams[currModule]["cmdParams"]["cats"]

        if self.updateMap:
            self.dialog.mapWin.UpdateMap(render=False, renderVector=False)

        if len(cats) <= 1:
            return 

        if checkedVal == "":
            self.CheckItem(key, False)
            return

        if currModule == "v.net.path" and flag:
            self._updateCheckedItems(index)

    def _updateCheckedItems(self, index):
        """!For v.net.path - max. just one checked start point and end point """
        alreadyChecked = []
        if index:
            checkedKey = self.GetItemData(index)
            checkedVal = self.itemDataMap[checkedKey][1]
            alreadyChecked.append(checkedVal)
        else:
            checkedKey = -1

        for iItem, item in enumerate(self.itemDataMap):
            itemKey = self.GetItemData(iItem)
            if (item[1] in alreadyChecked and checkedKey != iItem) \
               or not item[1]:
                self.CheckItem(itemKey, False)
            elif self.IsChecked(itemKey):
                alreadyChecked.append(item[1])

    def SetUpdateMap(self, updateMap):
        self.updateMap = updateMap


class SettingsDialog(wx.Dialog):
    def __init__(self, parent, id, title, pos=wx.DefaultPosition, size=wx.DefaultSize,
                 style=wx.DEFAULT_DIALOG_STYLE):
        """!Settings for v.net analysis dialog"""
        wx.Dialog.__init__(self, parent, id, title, pos, size, style)

        self.settings = {}
        maxValue = 1e8
        self.parent = parent

        self.colorsSetts = {
                            "line_color" : ["res_style", _("Line color:")],
                            "unused" : ["point_colors", _("Color for unused point:")], 
                            "used1cat" : ["point_colors", _("Color for Start/From/Source/Used point:")],
                            "used2cat" : ["point_colors", _("Color for End/To/Sink point:")],
                            "selected" : ["point_colors", _("Color for selected point:")]
                           }
        settsLabels = {} 

        for settKey, sett in self.colorsSetts.iteritems():
            settsLabels[settKey] = wx.StaticText(parent = self, id = wx.ID_ANY, label = sett[1])
            col = UserSettings.Get(group ='vnet', key = sett[0], subkey = settKey)        
            self.settings[settKey] = csel.ColourSelect(parent = self, id = wx.ID_ANY,
                                            colour = wx.Colour(col[0],
                                                               col[1],
                                                               col[2], 
                                                               255))

        self.sizeSetts = {
                          "line_width" : ["res_style", _("Line width:")],
                          "point_size" : ["point_symbol", _("Point size:")], 
                          "point_width" : ["point_symbol", _("Point width:")],
                          "snap_tresh" : ["other", _("Snapping treshold in pixels:")],
                          "max_hist_steps" : ["other", _("Maximum number of results in history:")]
                         }

        for settKey, sett in self.sizeSetts.iteritems():
            settsLabels[settKey] = wx.StaticText(parent = self, id = wx.ID_ANY, label = sett[1])
            self.settings[settKey] = wx.SpinCtrl(parent = self, id = wx.ID_ANY, min = 1, max = 50)
            size = int(UserSettings.Get(group = 'vnet', key = sett[0], subkey = settKey))
            self.settings[settKey].SetValue(size)


        # buttons
        self.btnSave = wx.Button(self, wx.ID_SAVE)
        self.btnApply = wx.Button(self, wx.ID_APPLY)
        self.btnClose = wx.Button(self, wx.ID_CLOSE)
        self.btnApply.SetDefault()

        # bindings
        self.btnApply.Bind(wx.EVT_BUTTON, self.OnApply)
        self.btnApply.SetToolTipString(_("Apply changes for the current session"))
        self.btnSave.Bind(wx.EVT_BUTTON, self.OnSave)
        self.btnSave.SetToolTipString(_("Apply and save changes to user settings file (default for next sessions)"))
        self.btnClose.Bind(wx.EVT_BUTTON, self.OnClose)
        self.btnClose.SetToolTipString(_("Close dialog"))

        #Layout

        self.SetMinSize(self.GetBestSize())

        sizer = wx.BoxSizer(wx.VERTICAL)

        styleBox = wx.StaticBox(parent = self, id = wx.ID_ANY,
                                label =" %s " % _("Analysis outcome line style:"))
        styleBoxSizer = wx.StaticBoxSizer(styleBox, wx.VERTICAL)

        gridSizer = wx.GridBagSizer(vgap = 1, hgap = 1)
        gridSizer.AddGrowableCol(1)

        row = 0
        gridSizer.Add(item =  settsLabels["line_color"], flag = wx.ALIGN_CENTER_VERTICAL, pos =(row, 0))
        gridSizer.Add(item = self.settings["line_color"],
                      flag = wx.ALIGN_RIGHT | wx.ALL, border = 5,
                      pos =(row, 1))
 
        row += 1
        gridSizer.Add(item =  settsLabels["line_width"], flag=wx.ALIGN_CENTER_VERTICAL, pos=(row, 0))
        gridSizer.Add(item = self.settings["line_width"],
                      flag = wx.ALIGN_RIGHT | wx.ALL, border = 5,
                      pos = (row, 1))
        styleBoxSizer.Add(item = gridSizer, flag = wx.EXPAND)

        ptsStyleBox = wx.StaticBox(parent = self, id = wx.ID_ANY,
                                   label =" %s " % _("Point style:"))
        ptsStyleBoxSizer = wx.StaticBoxSizer(ptsStyleBox, wx.VERTICAL)

        gridSizer = wx.GridBagSizer(vgap = 1, hgap = 1)
        gridSizer.AddGrowableCol(1)

        row = 0
        setts = dict(self.colorsSetts.items() + self.sizeSetts.items())

        settsOrder = ["selected", "used1cat", "used2cat", "unused", "point_size", "point_width"]
        for settKey in settsOrder:
            sett = setts[settKey]
            gridSizer.Add(item = settsLabels[settKey], flag = wx.ALIGN_CENTER_VERTICAL, pos =(row, 0))
            gridSizer.Add(item = self.settings[settKey],
                          flag = wx.ALIGN_RIGHT | wx.ALL, border = 5,
                          pos =(row, 1))  
            row += 1

        ptsStyleBoxSizer.Add(item = gridSizer, flag = wx.EXPAND)

        otherBox = wx.StaticBox(parent = self, id = wx.ID_ANY,
                                label =" %s " % _("Other settings"))
        otherBoxSizer = wx.StaticBoxSizer(otherBox, wx.VERTICAL)

        gridSizer = wx.GridBagSizer(vgap = 1, hgap = 1)
        gridSizer.AddGrowableCol(1)

        row = 0 #TODO for?
        gridSizer.Add(item = settsLabels["snap_tresh"], flag=wx.ALIGN_CENTER_VERTICAL, pos=(row, 0))
        gridSizer.Add(item = self.settings["snap_tresh"],
                      flag = wx.ALIGN_RIGHT | wx.ALL, border = 5,
                      pos = (row, 1))
        row += 1
        gridSizer.Add(item = settsLabels["max_hist_steps"], flag=wx.ALIGN_CENTER_VERTICAL, pos=(row, 0))
        gridSizer.Add(item = self.settings["max_hist_steps"],
                      flag = wx.ALIGN_RIGHT | wx.ALL, border = 5,
                      pos = (row, 1))
        otherBoxSizer.Add(item = gridSizer, flag = wx.EXPAND)

        btnSizer = wx.BoxSizer(wx.HORIZONTAL)
        btnSizer.Add(self.btnApply, flag = wx.LEFT | wx.RIGHT, border = 5)
        btnSizer.Add(self.btnSave, flag=wx.LEFT | wx.RIGHT, border=5)
        btnSizer.Add(self.btnClose, flag = wx.LEFT | wx.RIGHT, border = 5)

        sizer.Add(item = styleBoxSizer, flag = wx.EXPAND | wx.ALL, border = 5)
        sizer.Add(item = ptsStyleBoxSizer, flag = wx.EXPAND | wx.ALL, border = 5)
        sizer.Add(item = otherBoxSizer, flag = wx.EXPAND | wx.ALL, border = 5)
        sizer.Add(item = btnSizer, flag = wx.EXPAND | wx.ALL, border = 5, proportion = 0)    

        self.SetSizer(sizer)
        sizer.Fit(self)
     
    def OnSave(self, event):
        """!Button 'Save' pressed"""
        self.UpdateSettings()

        fileSettings = {}
        UserSettings.ReadSettingsFile(settings=fileSettings)
        fileSettings['vnet'] = UserSettings.Get(group='vnet')
        UserSettings.SaveToFile(fileSettings)

        self.Close()

    def UpdateSettings(self):

        UserSettings.Set(group ='vnet', key = "res_style", subkey ='line_width',
                         value = self.settings["line_width"].GetValue())

        for settKey, sett in self.colorsSetts.iteritems():
            col = tuple(self.settings[settKey].GetColour())
            UserSettings.Set(group = 'vnet', 
                             key = sett[0], 
                             subkey = settKey,
                             value = col)

        for settKey, sett in self.sizeSetts.iteritems():
            UserSettings.Set(group = 'vnet', key = sett[0], subkey = settKey, 
                             value = self.settings[settKey].GetValue())

        self.parent.SetPointDrawSettings()

        if not self.parent.tmpMaps.HasTmpVectMap("vnet_tmp_result"):
            self.parent.mapWin.UpdateMap(render=False, renderVector=False)
        elif self.parent.tmp_result.GetRenderLayer():
            cmd = self.parent.GetLayerStyle()
            self.parent.tmp_result.AddRenderLayer(cmd)
            self.parent.mapWin.UpdateMap(render=True, renderVector=True)#TODO optimization
        else:
            self.parent.mapWin.UpdateMap(render=False, renderVector=False)

    def OnApply(self, event):
        """!Button 'Apply' pressed"""
        self.UpdateSettings()
        #self.Close()

    def OnClose(self, event):
        """!Button 'Cancel' pressed"""
        self.Close()

class AddLayerDialog(wx.Dialog):
    def __init__(self, parent,id=wx.ID_ANY,
                 title =_("Add analysis result into layer tree"), style=wx.DEFAULT_DIALOG_STYLE):
        """!Adds vector map with analysis result into layer tree"""
        wx.Dialog.__init__(self, parent, id, title = _(title), style = style)

        self.panel = wx.Panel(parent = self)
       
        # text fields and it's captions
        self.vectSel = Select(parent = self.panel, type = 'vector', size = (-1, -1))
        self.vectSellabel = wx.StaticText(parent = self.panel, id = wx.ID_ANY,
                                          label = _("Map name:")) 

        # buttons
        self.btnCancel = wx.Button(self.panel, wx.ID_CANCEL)
        self.btnOk = wx.Button(self.panel, wx.ID_OK)
        self.btnOk.SetDefault()

        self.SetInitialSize((400, -1))
        self._layout()

    def _layout(self):

        sizer = wx.BoxSizer(wx.VERTICAL)

        box = wx.StaticBox (parent = self.panel, id = wx.ID_ANY,
                            label = "Added vector map")

        boxSizer = wx.StaticBoxSizer(box, wx.HORIZONTAL)

        boxSizer.Add(item = self.vectSellabel, 
                     flag = wx.ALIGN_CENTER_VERTICAL,
                     proportion = 0)

        boxSizer.Add(item = self.vectSel, proportion = 1,
                     flag = wx.EXPAND | wx.ALL, border = 5)

        sizer.Add(item = boxSizer, proportion = 1,
                  flag = wx.EXPAND | wx.ALL, border = 5)

        btnSizer = wx.StdDialogButtonSizer()
        btnSizer.AddButton(self.btnCancel)
        btnSizer.AddButton(self.btnOk)
        btnSizer.Realize()

        sizer.Add(item = btnSizer, proportion = 0,
                  flag = wx.ALIGN_RIGHT | wx.ALL, border = 5)

        self.panel.SetSizer(sizer)
        sizer.Fit(self)

class VnetTmpVectMaps:
    def __init__(self, parent):
        """!Class which creates, stores and destroys all tmp maps created during analysis"""
        self.tmpMaps = []
        self.parent = parent
        self.mapWin = self.parent.mapWin

    def AddTmpVectMap(self, mapName, endStr):
        
        currMapSet = grass.gisenv()['MAPSET']
        tmpMap = grass.find_file(name = mapName, 
                                 element = 'vector', 
                                 mapset = currMapSet)

        fullName = tmpMap["fullname"]
        if fullName:
            dlg = wx.MessageDialog(parent = self.parent,
                                   message = _("Temporary map %s  already exists.\n"  + 
                                               endStr) % fullName,
                                   caption = _("Overwrite map layer"),
                                   style = wx.YES_NO | wx.NO_DEFAULT |
                                   wx.ICON_QUESTION | wx.CENTRE)
                
            ret = dlg.ShowModal()
            dlg.Destroy()
                
            if ret == wx.ID_NO:
                return None
        else:
            fullName = mapName + "@" + currMapSet

        newVectMap = VectMap(self, fullName)
        self.tmpMaps.append(newVectMap)

        return newVectMap

    def HasTmpVectMap(self, vectMap):

        fullName = vectMap + "@" + grass.gisenv()['MAPSET']
        for vectTmpMap in self.tmpMaps:
            if vectTmpMap.GetVectMapName() == fullName:
                return True
        return False

    def GetTmpVectMap(self, vectMapName):

        for vectMap in self.tmpMaps:
            if vectMap.GetVectMapName() == vectMapName.strip():
                return vectMap
        return None

    def RemoveFromTmpMaps(self, vectMap):

        try:
            self.tmpMaps.remove(vectMap)
            return True
        except ValueError:
            return False

    def DeleteTmpMap(self, vectMap):

        vectMap.DeleteRenderLayer()
        RunCommand('g.remove', 
                    vect = vectMap.GetVectMapName())
        self.RemoveFromTmpMaps(vectMap)

    def DeleteAllTmpMaps(self):

        update = False
        for tmpMap in self.tmpMaps:
            RunCommand('g.remove', 
                        vect = tmpMap.GetVectMapName())
            if tmpMap.DeleteRenderLayer():
                update = True
        return update

class VectMap:
    def __init__(self, parent, fullName):
        """!Represents one temporary map"""
        self.fullName = fullName
        self.parent = parent
        self.renderLayer = None
        self.modifTime = None

    def __del__(self):

        self.DeleteRenderLayer()
   
    def AddRenderLayer(self, cmd = None):

        existsMap = grass.find_file(name = self.fullName, 
                                    element = 'vector', 
                                    mapset = grass.gisenv()['MAPSET'])

        if not existsMap["name"]:
            self.DeleteRenderLayer()
            return False

        if not cmd:
            cmd = []    
        cmd.insert(0, 'd.vect')
        cmd.append('map=%s' % self.fullName)

        if self.renderLayer:       
             self.DeleteRenderLayer()

        self.renderLayer = self.parent.mapWin.Map.AddLayer(type = "vector",  command = cmd,
                                                           l_active=True,    name = self.fullName, 
                                                           l_hidden = True,  l_opacity = 1.0, 
                                                           l_render = False,  pos = -1)
        return True

    def DeleteRenderLayer(self):
        if self.renderLayer: 
             self.parent.mapWin.Map.DeleteLayer(self.renderLayer)
             self.renderLayer = None
             return True
        return False

    def GetRenderLayer(self):
        return self.renderLayer

    def GetVectMapName(self):
        return self.fullName

    def SaveVectMapState(self):
  
        self.modifTime = self.GetLastModified()

    def VectMapState(self):

        if self.modifTime is None:#TODO 
            return -1       
        if self.modifTime != self.GetLastModified():
            return 0  
        return 1

    def GetLastModified(self):

        name = self.fullName.split("@")[0]
        headPath =  os.path.join(grass.gisenv()['GISDBASE'],
                                 grass.gisenv()['LOCATION_NAME'],
                                 grass.gisenv()['MAPSET'],
                                 "vector",
                                 name,
                                 "head")

        head = open(headPath, 'r')
        for line in head.readlines():
            i = line.find('MAP DATE:', )
            if i == 0:
               head.close()
               return line.split(':', 1)[1].strip()

        head.close()
        return ""

class History:
    def __init__(self, parent):

        self.maxHistSteps = 3
        self.currHistStep = 0
        self.histStepsNum = 0

        self.currHistStepData = {}

        self.newHistStepData = {}
        self.histFile = grass.tempfile()

        # key/value separator
        self.sep = ';'

    def __del__(self):
        grass.try_remove(self.histFile)

    def GetNext(self):

        self.currHistStep -= 1
        self.currHistStepData.clear()
        self.currHistStepData = self._getHistStepData(self.currHistStep)

        return self.currHistStepData

    def GetPrev(self):

        self.currHistStep += 1 
        self.currHistStepData.clear()
        self.currHistStepData = self._getHistStepData(self.currHistStep)

        return self.currHistStepData

    def GetStepsNum(self):
        return self.histStepsNum

    def GetCurrHistStep(self):
        return self.currHistStep

    def Add(self, key, subkey, value):#TODO

        if key not in self.newHistStepData:
            self.newHistStepData[key] = {}

        if type(subkey) == types.ListType:
            if subkey[0] not in self.newHistStepData[key]:
                self.newHistStepData[key][subkey[0]] = {}
            self.newHistStepData[key][subkey[0]][subkey[1]] = value
        else:
            self.newHistStepData[key][subkey] = value

    def SaveHistStep(self):

        self.maxHistSteps = UserSettings.Get(group ='vnet',
                                             key = 'other',
                                             subkey = 'max_hist_steps')
        self.currHistStep = 0 #TODO

        newHistFile = grass.tempfile()
        newHist = open(newHistFile, "w")

        self._saveNewHistStep(newHist)

        oldHist = open(self.histFile)
        removedHistData = self._savePreviousHist(newHist, oldHist)

        oldHist.close()
        newHist.close()
        grass.try_remove(self.histFile)
        self.histFile = newHistFile

        self.newHistStepData.clear() 

        return removedHistData

    def _savePreviousHist(self, newHist, oldHist):          

        newHistStep = False
        removedHistData = {}
        newHistStepsNum = self.histStepsNum

        for line in oldHist.readlines():
            if not line.strip():
                newHistStep = True
                newHistStepsNum += 1
                continue

            if newHistStep:
                newHistStep = False

                line = line.split("=")
                line[1] = str(newHistStepsNum)
                line = "=".join(line)

                if newHistStepsNum >= self.maxHistSteps:
                    removedHistStep = removedHistData[line] = {}
                    continue
                else:
                    newHist.write('%s%s%s' % (os.linesep, line, os.linesep))
                    self.histStepsNum = newHistStepsNum
            else:
                if newHistStepsNum >= self.maxHistSteps:
                    self._parseLine(line, removedHistStep)
                else:
                    newHist.write('%s' % line)                

        return removedHistData
            
    def _saveNewHistStep(self, newHist):
 
        newHist.write('%s%s%s' % (os.linesep, "history step=0", os.linesep))  
        for key in self.newHistStepData.keys():
            subkeys =  self.newHistStepData[key].keys()
            newHist.write('%s%s' % (key, self.sep))
            for idx in range(len(subkeys)):
                value =  self.newHistStepData[key][subkeys[idx]]
                if type(value) == types.DictType:
                    if idx > 0:
                        newHist.write('%s%s%s' % (os.linesep, key, self.sep))
                    newHist.write('%s%s' % (subkeys[idx], self.sep))
                    kvalues =  self.newHistStepData[key][subkeys[idx]].keys()
                    srange = range(len(kvalues))
                    for sidx in srange:
                        svalue = self._parseValue(self.newHistStepData[key][subkeys[idx]][kvalues[sidx]])
                        newHist.write('%s%s%s' % (kvalues[sidx], self.sep, svalue))
                        if sidx < len(kvalues) - 1:
                            newHist.write('%s' % self.sep)
                else:
                    if idx > 0 and \
                            type( self.newHistStepData[key][subkeys[idx - 1]]) == types.DictType:
                        newHist.write('%s%s%s' % (os.linesep, key, self.sep))
                    value = self._parseValue(self.newHistStepData[key][subkeys[idx]])
                    newHist.write('%s%s%s' % (subkeys[idx], self.sep, value))
                    if idx < len(subkeys) - 1 and \
                            type(self.newHistStepData[key][subkeys[idx + 1]]) != types.DictType:
                        newHist.write('%s' % self.sep)
            newHist.write(os.linesep)
        self.histStepsNum = 0

    def _parseValue(self, value, read = False):

        if read: # -> read data (cast values)

            if value:
                if value[0] == '[' and value[-1] == ']':# TODO, possible wrong interpretation
                    value = value[1:-1].split(',')
                    value = map(self._castValue, value)
                    return value

            if value == 'True':
                value = True
            elif value == 'False':
                value = False
            elif value == 'None':
                value = None
            elif ':' in value: # -> color
                try:
                    value = tuple(map(int, value.split(':')))
                except ValueError: # -> string
                    pass
            else:
                try:
                    value = int(value)
                except ValueError:
                    try:
                        value = float(value)
                    except ValueError:
                        pass
        else: # -> write data
            if type(value) == type(()): # -> color
                value = str(value[0]) + ':' +\
                    str(value[1]) + ':' + \
                    str(value[2])
                
        return value

    def _castValue(self, value):
        try:
            value = int(value)
        except ValueError:
            try:
                value = float(value)
            except ValueError:
                value = value[1:-1]

        return value

    def _getHistStepData(self, histStep):          
        
        hist = open(self.histFile)
        histStepData = {}

        newHistStep = False
        isSearchedHistStep = False
        for line in hist.readlines():

            if  not line.strip() and isSearchedHistStep:
                 break
            elif not line.strip():
                newHistStep = True
                continue
            elif isSearchedHistStep:
                self._parseLine(line, histStepData)

            if newHistStep:
                line = line.split("=")
                if int(line[1]) == histStep:
                    isSearchedHistStep = True
                newHistStep = False

        hist.close()
        return histStepData

    def _parseLine(self, line, histStepData):

            line = line.rstrip('%s' % os.linesep).split(self.sep)
            key = line[0]
            kv = line[1:]
            idx = 0
            subkeyMaster = None
            if len(kv) % 2 != 0: # multiple (e.g. nviz)
                subkeyMaster = kv[0]
                del kv[0]
            idx = 0
            while idx < len(kv):
                if subkeyMaster:
                    subkey = [subkeyMaster, kv[idx]]
                else:
                    subkey = kv[idx]
                value = kv[idx+1]
                value = self._parseValue(value, read = True)
                if key not in histStepData:
                    histStepData[key] = {}

                if type(subkey) == types.ListType:
                    if subkey[0] not in histStepData[key]:
                        histStepData[key][subkey[0]] = {}
                    histStepData[key][subkey[0]][subkey[1]] = value
                else:
                    histStepData[key][subkey] = value
                idx += 2

#TODO ugly hack - just for GMConsole to be satisfied 
class CmdPanelHack:
     def createCmd(self, ignoreErrors = False, ignoreRequired = False):
        pass



