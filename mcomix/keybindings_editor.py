# -*- coding: utf-8 -*-

""" Configuration tree view for the preferences dialog to edit keybindings. """

from gi.repository import Gtk

from mcomix import keybindings
from mcomix.i18n import _


class KeybindingEditorWindow(Gtk.ScrolledWindow):

    def __init__(self, keymanager):
        """ @param keymanager: KeybindingManager instance. """
        super(KeybindingEditorWindow, self).__init__()
        self.set_border_width(5)
        self.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.ALWAYS)

        self.keymanager = keymanager

        accel_column_num = max([
            len(self.keymanager.get_bindings_for_action(action))
            for action in list(keybindings.BINDING_INFO.keys())
        ])
        accel_column_num = self.accel_column_num = max([3, accel_column_num])

        # Human name, action name, true value, shortcut 1, shortcut 2, ...
        model = [str, str, 'gboolean']
        model.extend( [str, ] * accel_column_num)

        treestore = self.treestore = Gtk.TreeStore(*model)
        self.refresh_model()

        treeview = Gtk.TreeView(treestore)

        tvcol1 = Gtk.TreeViewColumn(_("Name"))
        treeview.append_column(tvcol1)
        cell1 = Gtk.CellRendererText()
        tvcol1.pack_start(cell1, True)
        tvcol1.set_attributes(cell1, text=0)

        for idx in range(0, self.accel_column_num):
            tvc = Gtk.TreeViewColumn(_("Key %d") % (idx +1))
            treeview.append_column(tvc)
            accel_cell = Gtk.CellRendererAccel()
            accel_cell.connect("accel-edited", self.get_on_accel_edited(idx))
            accel_cell.connect("accel-cleared", self.get_on_accel_cleared(idx))
            tvc.pack_start(accel_cell, True)
            tvc.add_attribute(accel_cell, "text", 3 + idx)
            tvc.add_attribute(accel_cell, "editable", 2)

        # Allow sorting on the column
        tvcol1.set_sort_column_id(0)

        self.add_with_viewport(treeview)

    def refresh_model(self):
        """ Initializes the model from data provided by the keybinding
        manager. """
        self.treestore.clear()
        section_order = list(set(d['group']
             for d in list(keybindings.BINDING_INFO.values())))
        section_order.sort()
        section_parent_map = {}
        for section_name in section_order:
            row = [section_name, None, False]
            row.extend( [None,] * self.accel_column_num)
            section_parent_map[section_name] =  self.treestore.append(
                None, row
            )

        action_treeiter_map = self.action_treeiter_map = {}
        # Sort actions by action name
        actions = sorted(list(keybindings.BINDING_INFO.items()),
                key=lambda item: item[1]['title'])
        for action_name, action_data in actions:
            title = action_data['title']
            group_name = action_data['group']
            old_bindings = self.keymanager.get_bindings_for_action(action_name)
            acc_list =  ["", ] * self.accel_column_num
            for idx in range(0, self.accel_column_num):
                if len(old_bindings) > idx:
                    acc_list[idx] = Gtk.accelerator_name(*old_bindings[idx])

            row = [title, action_name, True]
            row.extend(acc_list)
            treeiter = self.treestore.append(
                section_parent_map[group_name],
                row
            )
            action_treeiter_map[action_name] = treeiter

    def _refresh_action(self, action_name: str) -> None:
        """Display the manager's canonical order after replacement or removal."""
        iterator = self.action_treeiter_map[action_name]
        bindings = self.keymanager.get_bindings_for_action(action_name)
        for column in range(self.accel_column_num):
            accel = (Gtk.accelerator_name(*bindings[column])
                     if column < len(bindings) else '')
            self.treestore.set_value(iterator, column + 3, accel)

    def get_on_accel_edited(self, column):
        def on_accel_edited(renderer, path, accel_key, accel_mods, hardware_keycode):
            iterator = self.treestore.get_iter(path)
            old_accel = self.treestore.get_value(iterator, column + 3)
            new_accel = Gtk.accelerator_name(accel_key, accel_mods)
            action_name = self.treestore.get_value(iterator, 1)
            affected_action = self.keymanager.edit_accel(action_name, new_accel,
                                                        old_accel)
            self._refresh_action(action_name)
            if affected_action is not None and affected_action != action_name:
                self._refresh_action(affected_action)
        return on_accel_edited

    def get_on_accel_cleared(self, column):
        def on_accel_cleared(renderer, path, *args):
            iterator = self.treestore.get_iter(path)
            accel = self.treestore.get_value(iterator, column + 3)
            action_name = self.treestore.get_value(iterator, 1)
            if accel:
                self.keymanager.clear_accel(action_name, accel)
                self._refresh_action(action_name)
        return on_accel_cleared


# vim: expandtab:sw=4:ts=4
