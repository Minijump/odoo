# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger
from odoo import Command
import contextlib


class TestRules(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        ObjCateg = cls.env['test_access_right.obj_categ']
        SomeObj = cls.env['test_access_right.some_obj']
        cls.categ = ObjCateg.create({'name': 'Food'})
        cls.allowed = SomeObj.create({'val': 1, 'categ_id': cls.categ.id})
        cls.forbidden = SomeObj.create({'val': -1, 'categ_id': cls.categ.id})
        # create a global rule forbidding access to records with a negative
        # (or zero) val
        cls.env['ir.rule'].create({
            'name': 'Forbid negatives',
            'model_id': cls.env.ref('test_access_rights.model_test_access_right_some_obj').id,
            'domain_force': "[('val', '>', 0)]"
        })
        # create a global rule that forbid access to records without
        # categories, the search is part of the test
        cls.env['ir.rule'].create({
            'name': 'See all categories',
            'model_id': cls.env.ref('test_access_rights.model_test_access_right_some_obj').id,
            'domain_force': "[('categ_id', 'in', user.env['test_access_right.obj_categ'].search([]).ids)]"
        })

    @mute_logger('odoo.addons.base.models.ir_rule')
    def test_basic_access(self):
        env = self.env(user=self.env.ref('base.public_user'))
        allowed = self.allowed.with_env(env)
        forbidden = self.forbidden.with_env(env)

        # this one should not blow up
        self.assertEqual(allowed.val, 1)

        # but this one should
        allowed.invalidate_model(['val'])
        with self.assertRaises(AccessError):
            self.assertEqual(forbidden.val, -1)

    @mute_logger('odoo.addons.base.models.ir_rule')
    def test_group_rule(self):
        env = self.env(user=self.env.ref('base.public_user'))
        allowed = self.allowed.with_env(env)
        forbidden = self.forbidden.with_env(env)

        # we forbid access to the public group, to which the public user belongs
        self.env['ir.rule'].create({
            'name': 'Forbid public group',
            'model_id': self.env.ref('test_access_rights.model_test_access_right_some_obj').id,
            'groups': [Command.set([self.env.ref('base.group_public').id])],
            'domain_force': "[(0, '=', 1)]"
        })

        # everything should blow up
        (allowed + forbidden).invalidate_model(['val'])
        with self.assertRaises(AccessError):
            self.assertEqual(forbidden.val, -1)
        with self.assertRaises(AccessError):
            self.assertEqual(allowed.val, 1)

    def test_many2many(self):
        """ Test assignment of many2many field where rules apply. """
        ids = [self.allowed.id, self.forbidden.id]

        # create container as superuser, connected to all some_objs
        container_admin = self.env['test_access_right.container'].create({'some_ids': [Command.set(ids)]})
        self.assertItemsEqual(container_admin.some_ids.ids, ids)

        # check the container as the public user
        container_user = container_admin.with_user(self.env.ref('base.public_user'))
        container_user.invalidate_model(['some_ids'])
        self.assertItemsEqual(container_user.some_ids.ids, [self.allowed.id])

        # this should fail
        with self.assertRaises(AccessError):
            container_user.write({'some_ids': [Command.set(ids)]})

        container_admin.write({'some_ids': [Command.set(ids)]})
        container_user.invalidate_model(['some_ids'])
        self.assertItemsEqual(container_user.some_ids.ids, [self.allowed.id])
        container_admin.invalidate_model(['some_ids'])
        self.assertItemsEqual(container_admin.some_ids.ids, ids)

        # this removes all records
        container_user.write({'some_ids': [Command.clear()]})
        container_user.invalidate_model(['some_ids'])
        self.assertItemsEqual(container_user.some_ids.ids, [])
        container_admin.invalidate_model(['some_ids'])
        self.assertItemsEqual(container_admin.some_ids.ids, [])

    def test_access_rule_performance(self):
        env = self.env(user=self.env.ref('base.public_user'))
        Model = env['test_access_right.some_obj']
        # cache warmup for check() in 'ir.model.access'
        Model.check_access('read')
        with self.assertQueryCount(0):
            Model._filtered_access('read')

    def test_no_context_in_ir_rules(self):
        """ The context should not impact the ir rules. """
        ObjCateg = self.env['test_access_right.obj_categ']
        SomeObj = self.env['test_access_right.some_obj']

        # validate the effect of context on category search, there are
        # no existing media category
        self.assertTrue(ObjCateg.search([]))
        self.assertFalse(ObjCateg.with_context(only_media=True).search([]))

        # record1 is food and is accessible with an empy context
        self.env.registry.clear_cache()
        records = SomeObj.search([('id', '=', self.allowed.id)])
        self.assertTrue(records)

        # it should also be accessible as the context is not used when
        # searching for SomeObjs
        self.env.registry.clear_cache()
        records = SomeObj.with_context(only_media=True).search([('id', '=', self.allowed.id)])
        self.assertTrue(records)

    def test_check_access_rule_with_inherits(self):
        """
        For models in `_inherits`, verify that both methods `check_access`
        and `_search` check the rules from parent models.
        """
        ChildModel = self.env['test_access_right.inherits']
        allowed_child, __ = children = ChildModel.create([
            {'some_id': self.allowed.id}, {'some_id': self.forbidden.id},
        ])

        user = self.env.ref('base.public_user')
        search_result = children.with_user(user).search([('id', 'in', children.ids)], order='id')
        filter_result = children.with_user(user)._filtered_access('read')

        self.assertEqual(search_result, allowed_child)
        self.assertEqual(filter_result, allowed_child)

    def test_flush_with_inherits(self):
        """
        For models with `_inherits`, verify that fields of the rules from inherited models
        are flushed correctly.
        """
        ChildModel = self.env['test_access_right.inherits']
        child = ChildModel.create([{'some_id': self.allowed.id}])
        self.env.flush_all()

        self.env['ir.rule'].create({
            'name': 'Forbid 0 value',
            'model_id': self.env['ir.model']._get('test_access_right.some_obj').id,
            'domain_force': str([('val', '!=', 0)]),
        })

        user = self.env.ref('base.public_user')

        # the parent record is accessible, so is the child record
        search_result = ChildModel.with_user(user).search([('id', '=', child.id)], order='id')
        self.assertEqual(search_result, child)

        # make the parent record inaccessible, and verify that the child record
        # becomes inaccessible, too
        self.allowed.val = 0
        search_result = ChildModel.with_user(user).search([('id', '=', child.id)], order='id')
        self.assertEqual(search_result, ChildModel)

    def test_domain_constrains(self):
        """ An error should be raised if domain is not correct """

        rule = self.env['ir.rule'].create({
            'name': 'Test record rule',
            'model_id': self.env.ref('test_access_rights.model_test_access_right_some_obj').id,
            'domain_force': [],
        })
        invalid_domains = [
            'A really bad domain!',
            [(1, '!=', 1)],
            [('non_existing_field', '=', 'value')],
        ]

        for domain in invalid_domains:
            with self.assertRaisesRegex(ValidationError, 'Invalid domain'):
                rule.domain_force = domain

        valid_domains = [
            False,
            [(1, '=', 1)],
            [('val', '=', 12)],
        ]
        for domain in valid_domains:
            # no error is raised
            rule.domain_force = domain

    def test_propagated_access_with_group_rule(self):
        """ Test that access propagation from parent works for a group rule. """
        children_model_name = 'test_access_right.propagated_children'
        child_record_access = self.env[children_model_name].create_children(parent_has_access=True)
        child_record_no_access = self.env[children_model_name].create_children(parent_has_access=False)

        children_model = self.env['ir.model']._get(children_model_name)
        self.env['ir.rule'].create({
            'name': 'Child rule',
            'model_id': children_model.id,
            'access_propagation_field_id': self.env['ir.model.fields'].search(
                [('name', '=', 'parent_id'), ('model_id', '=', children_model.id)], limit=1
            ).id,
            'groups': [Command.link(self.env.ref('test_access_rights.test_group_propagation').id)],
        })

        propagation_user = self.env.ref('test_access_rights.simple_propagation_user')
        simple_user = self.env.ref('test_access_rights.simple_user')
        for operation in ['read', 'write', 'create', 'unlink']:
            self.assertTrue(child_record_access.with_user(propagation_user).has_access(operation))
            self.assertFalse(child_record_no_access.with_user(propagation_user).has_access(operation))
            self.assertTrue(child_record_access.with_user(simple_user).has_access(operation))
            self.assertTrue(child_record_no_access.with_user(simple_user).has_access(operation))

    def test_propagated_access_with_global_rule(self):
        """ Test that access propagation from parent works for a global rule. """
        children_model_name = 'test_access_right.propagated_children'
        child_record_access = self.env[children_model_name].create_children(parent_has_access=True)
        child_record_no_access = self.env[children_model_name].create_children(parent_has_access=False)

        children_model = self.env['ir.model']._get(children_model_name)
        self.env['ir.rule'].create({
            'name': 'Child rule',
            'model_id': children_model.id,
            'access_propagation_field_id': self.env['ir.model.fields'].search(
                [('name', '=', 'parent_id'), ('model_id', '=', children_model.id)], limit=1
            ).id,
        })

        propagation_user = self.env.ref('test_access_rights.simple_propagation_user')
        simple_user = self.env.ref('test_access_rights.simple_user')
        for operation in ['read', 'write', 'create', 'unlink']:
            self.assertTrue(child_record_access.with_user(simple_user).has_access(operation))
            self.assertFalse(child_record_no_access.with_user(simple_user).has_access(operation))
            self.assertTrue(child_record_access.with_user(propagation_user).has_access(operation))
            self.assertFalse(child_record_no_access.with_user(propagation_user).has_access(operation))

    def test_propagated_access_prevent_infinite_recursion_for_group_rules(self):
        """ 
        Test that infinite recursion is prevented for rules' propagation. 
        In case of circular references, the rule should be considered as False.

        In this case, only the rule from 'parent' model will be set to False.
        Since another one (test_access_right_propagate_parent_rule) provides access,
        The user can have access via this rule. (same as if recursion rule did not exist)
        """
        children_model_name = 'test_access_right.propagated_children'
        child_record_access = self.env[children_model_name].create_children(parent_has_access=True)
        child_record_no_access = self.env[children_model_name].create_children(parent_has_access=False)

        children_model = self.env['ir.model']._get(children_model_name)
        parent_model = self.env['ir.model']._get('test_access_right.propagated_parent')
        self.env['ir.rule'].create({
            'name': 'Child rule',
            'model_id': children_model.id,
            'access_propagation_field_id': self.env['ir.model.fields'].search(
                [('name', '=', 'parent_id'), ('model_id', '=', children_model.id)], limit=1
            ).id,
            'groups': [Command.link(self.env.ref('test_access_rights.test_group_propagation').id)],
        })
        self.env['ir.rule'].create({
            'name': 'Parent rule',
            'model_id': parent_model.id,
            'access_propagation_field_id': self.env['ir.model.fields'].search(
                [('name', '=', 'child_ids'), ('model_id', '=', parent_model.id)], limit=1
            ).id,
            'groups': [Command.link(self.env.ref('test_access_rights.test_group_propagation').id)],
        })

        propagation_user = self.env.ref('test_access_rights.simple_propagation_user')
        simple_user = self.env.ref('test_access_rights.simple_user')
        for operation in ['read', 'write', 'create', 'unlink']:
            self.assertTrue(child_record_access.with_user(simple_user).has_access(operation))
            self.assertTrue(child_record_no_access.with_user(simple_user).has_access(operation))
            self.assertTrue(child_record_access.with_user(propagation_user).has_access(operation))
            self.assertFalse(child_record_no_access.with_user(propagation_user).has_access(operation))

    def test_propagated_access_prevent_infinite_recursion_for_global_rules(self):
        """ 
        Test that infinite recursion is prevented for rules' propagation. 
        In case of circular references, the rule should be considered as False.
        In this case, as the rule is global, setting it to False means no user will have access.
        """
        children_model_name = 'test_access_right.propagated_children'
        child_record_access = self.env[children_model_name].create_children(parent_has_access=True)
        child_record_no_access = self.env[children_model_name].create_children(parent_has_access=False)

        children_model = self.env['ir.model']._get(children_model_name)
        parent_model = self.env['ir.model']._get('test_access_right.propagated_parent')
        self.env['ir.rule'].create({
            'name': 'Child rule',
            'model_id': children_model.id,
            'access_propagation_field_id': self.env['ir.model.fields'].search(
                [('name', '=', 'parent_id'), ('model_id', '=', children_model.id)], limit=1
            ).id,
        })
        self.env['ir.rule'].create({
            'name': 'Parent rule',
            'model_id': parent_model.id,
            'access_propagation_field_id': self.env['ir.model.fields'].search(
                [('name', '=', 'child_ids'), ('model_id', '=', parent_model.id)], limit=1
            ).id,
        })

        propagation_user = self.env.ref('test_access_rights.simple_propagation_user')
        simple_user = self.env.ref('test_access_rights.simple_user')
        for operation in ['read', 'write', 'create', 'unlink']:
            self.assertFalse(child_record_access.with_user(simple_user).has_access(operation))
            self.assertFalse(child_record_no_access.with_user(simple_user).has_access(operation))
            self.assertFalse(child_record_access.with_user(propagation_user).has_access(operation))
            self.assertFalse(child_record_no_access.with_user(propagation_user).has_access(operation))

    def test_propagated_access_multiple_propagation_levels(self):
        """ 
        Test that access propagation works correctly for the following case:
            - parent record -> child record -> brother record
        """
        children_model_name = 'test_access_right.propagated_children'
        children_record_access = self.env[children_model_name].create_children(parent_has_access=True)
        brother_record_access = self.env[children_model_name].create({'brother_id': children_record_access.id})
        children_record_no_access = self.env[children_model_name].create_children(parent_has_access=False)
        brother_record_no_access = self.env[children_model_name].create({'brother_id': children_record_no_access.id})

        children_model = self.env['ir.model']._get(children_model_name)
        self.env['ir.rule'].create({
            'name': 'Child rule',
            'model_id': children_model.id,
            'access_propagation_field_id': self.env['ir.model.fields'].search(
                [('name', '=', 'parent_id'), ('model_id', '=', children_model.id)], limit=1
            ).id,
            'groups': [Command.link(self.env.ref('test_access_rights.test_group_propagation').id)],
        })
        self.env['ir.rule'].create({
            'name': 'Brother rule',
            'model_id': children_model.id,
            'access_propagation_field_id': self.env['ir.model.fields'].search(
                [('name', '=', 'brother_id'), ('model_id', '=', children_model.id)], limit=1
            ).id,
            'groups': [Command.link(self.env.ref('test_access_rights.test_group_propagation').id)],
        })

        propagation_user = self.env.ref('test_access_rights.simple_propagation_user')
        for operation in ['read', 'write', 'create', 'unlink']:
            self.assertTrue(children_record_access.with_user(propagation_user).has_access(operation))
            self.assertTrue(brother_record_access.with_user(propagation_user).has_access(operation))
            self.assertFalse(children_record_no_access.with_user(propagation_user).has_access(operation))
            self.assertFalse(brother_record_no_access.with_user(propagation_user).has_access(operation))

    def test_only_domain_or_access_propagation(self):
        """ A rule should not have both a domain and access propagation. """
        test_model = self.env['ir.model']._get('test_access_right.some_obj')
        with self.assertRaisesRegex(ValidationError, 'A rule cannot have both a domain and a propagation field.'):
            self.env['ir.rule'].create({
                'name': 'Test record rule',
                'model_id': test_model.id,
                'domain_force': "[(1, '=', 1)]",
                'access_propagation_field_id': self.env['ir.model.fields'].search([('name', '=', 'parent_id'), ('model_id', '=', test_model.id)], limit=1).id,
            })

    @mute_logger('odoo.addons.base.models.ir_rule')
    def test_ir_rule_cache_after_error(self):
        NB_RECORD = 14  # At least twice 6, 6 is used by _make_access_error
        # copy the forbidden record 15 times
        SomeObj = self.env['test_access_right.some_obj']
        forbiddens = SomeObj.create([{'val': -1, 'categ_id': self.categ.id}] * NB_RECORD)
        forbiddens.invalidate_model()

        env = self.env(user=self.env.ref('base.public_user'))
        forbiddens = forbiddens.with_env(env)
        forbiddens.browse().check_access('read')

        # Don't use assertRaise since it invalidates the cache
        # and it is what we want to test.
        with contextlib.suppress(AccessError):
            forbiddens.check_access('read')
            self.fail('Previous line should raise AccessError')

        with contextlib.suppress(AccessError):
            forbiddens[0].val
            self.fail('Previous line should raise AccessError')

        with contextlib.suppress(AccessError):
            forbiddens[NB_RECORD - 1].val
            self.fail('Previous line should raise AccessError')
