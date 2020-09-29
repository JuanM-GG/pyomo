#  ___________________________________________________________________________
#
#  Pyomo: Python Optimization Modeling Objects
#  Copyright 2017 National Technology and Engineering Solutions of Sandia, LLC
#  Under the terms of Contract DE-NA0003525 with National Technology and
#  Engineering Solutions of Sandia, LLC, the U.S. Government retains certain
#  rights in this software.
#  This software is distributed under the 3-clause BSD License.
#  ___________________________________________________________________________

import os
import pyutilib.th as unittest
import pyomo.environ as pyo

from pyomo.contrib.pynumero.dependencies import (
    numpy as np, numpy_available, scipy_sparse as spa, scipy_available
)
if not (numpy_available and scipy_available):
    raise unittest.SkipTest("Pynumero needs scipy and numpy to run NLP tests")

from pyomo.contrib.pynumero.asl import AmplInterface
if not AmplInterface.available():
    raise unittest.SkipTest(
        "Pynumero needs the ASL extension to run CyIpoptSolver tests")

try:
    import ipopt
except ImportError:
    raise unittest.SkipTest("Pynumero needs cyipopt to run CyIpoptSolver tests")

from ..external_grey_box import ExternalGreyBoxModel, ExternalGreyBoxBlock, _ExternalGreyBoxModelHelper
from ..pyomo_nlp import PyomoGreyBoxNLP

# set of external models for testing
# basic model is a simple pipe sequence with nonlinear pressure drop
# Pin -> P1 -> P2 -> P3 -> Pout
#
# We will assume that we have an external model to compute
# the pressure drop in this sequence of pipes, where the dP
# is given by c*F^2
#
# There are several ways to format this.
# Model 1: Use the "external model" to compute the output pressure
#   no equalities, 1 output
#   u = [Pin, c, F]
#   o = [Pout]
#   h_eq(u) = {empty}
#   h_o(u) = [Pin - 4*c*F^2]
#
# Model 2: Same as model 1, but treat Pout as an input to be converged by the optimizer
#   1 equality, no outputs
#   u = [Pin, c, F, Pout]
#   o = {empty}
#   h_eq(u) = [Pout - (Pin - 4*c*F^2]
#   h_o(u) = {empty}
#
# Model 3: Use the "external model" to compute the output pressure and the pressure
#          at node 2 (e.g., maybe we have a measurement there we want to match)
#   no equalities, 2 outputs
#   u = [Pin, c, F]
#   o = [P2, Pout]
#   h_eq(u) = {empty}
#   h_o(u) = [Pin - 2*c*F^2]
#            [Pin - 4*c*F^2]
#
# Model 4: Same as model 2, but treat P2, and Pout as an input to be converged by the optimizer
#   2 equality, no outputs
#   u = [Pin, c, F, P2, Pout]
#   o = {empty}
#   h_eq(u) = [P2 - (Pin - 2*c*F^2]
#             [Pout - (P2 - 2*c*F^2]
#   h_o(u) = {empty}

# Model 4: Same as model 2, but treat P2 as an input to be converged by the solver
#   u = [Pin, c, F, P2]
#   o = [Pout]
#   h_eq(u) = P2 - (Pin-2*c*F^2)]
#   h_o(u) = [Pin - 4*c*F^2] (or could also be [P2 - 2*c*F^2])
#
# Model 5: treat all "internal" variables as "inputs", equality and output equations
#   u = [Pin, c, F, P1, P2, P3]
#   o = [Pout]
#    h_eq(u) = [
#               P1 - (Pin - c*F^2);
#               P2 - (P1 - c*F^2);
#               P3 - (P2 - c*F^2);
#              ]
#   h_o(u) = [P3 - c*F^2] (or could also be [Pin - 4*c*F^2] or [P1 - 3*c*F^2] or [P2 - 2*c*F^2])
# 
# Model 6: treat all variables as "inputs", equality only, and no output equations
#   u = [Pin, c, F, P1, P2, P3, Pout]
#   o = {empty}
#   h_eq(u) = [
#               P1 - (Pin - c*F^2);
#               P2 - (P1 - c*F^2);
#               P3 - (P2 - c*F^2);
#               Pout = (P3 - c*F^2);
#              ]
#   h_o(u) = {empty}
#
class PressureDropSingleOutput(ExternalGreyBoxModel):
    def __init__(self):
        self._input_names = ['Pin', 'c', 'F']
        self._input_values = np.zeros(3, dtype=np.float64)
        self._output_names = ['Pout']

    def input_names(self):
        return self._input_names

    def equality_constraint_names(self):
        return []

    def output_names(self):
        return self._output_names

    def set_input_values(self, input_values):
        assert len(input_values) == 3
        np.copyto(self._input_values, input_values)

    def evaluate_equality_constraints(self):
        raise NotImplementedError('This method should not be called for this model.')

    def evaluate_outputs(self):
        Pin = self._input_values[0]
        c = self._input_values[1]
        F = self._input_values[2]
        Pout = Pin - 4*c*F**2
        return np.asarray([Pout], dtype=np.float64)

    def evaluate_jacobian_equality_constraints(self):
        raise NotImplementedError('This method should not be called for this model.')

    def evaluate_jacobian_outputs(self):
        c = self._input_values[1]
        F = self._input_values[2]
        irow = np.asarray([0, 0, 0], dtype=np.int64)
        jcol = np.asarray([0, 1, 2], dtype=np.int64)
        nonzeros = np.asarray([1, -4*F**2, -4*c*2*F], dtype=np.float64)
        jac = spa.coo_matrix((nonzeros, (irow, jcol)), shape=(1,3))
        return jac

class PressureDropSingleEquality(ExternalGreyBoxModel):
    #   u = [Pin, c, F, Pout]
    #   o = {empty}
    #   h_eq(u) = [Pout - (Pin - 4*c*F^2]
    #   h_o(u) = {empty}
    def __init__(self):
        self._input_names = ['Pin', 'c', 'F', 'Pout']
        self._input_values = np.zeros(4, dtype=np.float64)
        self._equality_constraint_names = ['pdrop']

    def input_names(self):
        return self._input_names

    def equality_constraint_names(self):
        return self._equality_constraint_names

    def output_names(self):
        return []

    def set_input_values(self, input_values):
        assert len(input_values) == 4
        np.copyto(self._input_values, input_values)

    def evaluate_equality_constraints(self):
        Pin = self._input_values[0]
        c = self._input_values[1]
        F = self._input_values[2]
        Pout = self._input_values[3]
        return np.asarray([Pout - (Pin - 4*c*F**2)], dtype=np.float64)

    def evaluate_outputs(self):
        raise NotImplementedError('This method should not be called for this model.')

    def evaluate_jacobian_equality_constraints(self):
        c = self._input_values[1]
        F = self._input_values[2]
        irow = np.asarray([0, 0, 0, 0], dtype=np.int64)
        jcol = np.asarray([0, 1, 2, 3], dtype=np.int64)
        nonzeros = np.asarray([-1, 4*F**2, 4*2*c*F, 1], dtype=np.float64)
        jac = spa.coo_matrix((nonzeros, (irow, jcol)), shape=(1,4))
        return jac

    def evaluate_jacobian_outputs(self):
        raise NotImplementedError('This method should not be called for this model.')

class PressureDropTwoOutputs(ExternalGreyBoxModel):
    #   u = [Pin, c, F]
    #   o = [P2, Pout]
    #   h_eq(u) = {empty}
    #   h_o(u) = [Pin - 2*c*F^2]
    #            [Pin - 4*c*F^2]
    def __init__(self):
        self._input_names = ['Pin', 'c', 'F']
        self._input_values = np.zeros(3, dtype=np.float64)
        self._output_names = ['P2', 'Pout']

    def input_names(self):
        return self._input_names

    def equality_constraint_names(self):
        return []

    def output_names(self):
        return self._output_names

    def set_input_values(self, input_values):
        assert len(input_values) == 3
        np.copyto(self._input_values, input_values)

    def evaluate_equality_constraints(self):
        raise NotImplementedError('This method should not be called for this model.')

    def evaluate_outputs(self):
        Pin = self._input_values[0]
        c = self._input_values[1]
        F = self._input_values[2]
        P2 = Pin - 2*c*F**2
        Pout = Pin - 4*c*F**2
        return np.asarray([P2, Pout], dtype=np.float64)

    def evaluate_jacobian_equality_constraints(self):
        raise NotImplementedError('This method should not be called for this model.')

    def evaluate_jacobian_outputs(self):
        c = self._input_values[1]
        F = self._input_values[2]
        irow = np.asarray([0, 0, 0, 1, 1, 1], dtype=np.int64)
        jcol = np.asarray([0, 1, 2, 0, 1, 2], dtype=np.int64)
        nonzeros = np.asarray([1, -2*F**2, -2*c*2*F, 1, -4*F**2, -4*c*2*F], dtype=np.float64)
        jac = spa.coo_matrix((nonzeros, (irow, jcol)), shape=(2,3))
        return jac

class PressureDropTwoEqualities(ExternalGreyBoxModel):
    #   u = [Pin, c, F, P2, Pout]
    #   o = {empty}
    #   h_eq(u) = [P2 - (Pin - 2*c*F^2]
    #             [Pout - (P2 - 2*c*F^2]
    #   h_o(u) = {empty}
    def __init__(self):
        self._input_names = ['Pin', 'c', 'F', 'P2', 'Pout']
        self._input_values = np.zeros(5, dtype=np.float64)
        self._equality_constraint_names = ['pdrop2', 'pdropout']

    def input_names(self):
        return self._input_names

    def equality_constraint_names(self):
        return self._equality_constraint_names

    def output_names(self):
        return []

    def set_input_values(self, input_values):
        assert len(input_values) == 5
        np.copyto(self._input_values, input_values)

    def evaluate_equality_constraints(self):
        Pin = self._input_values[0]
        c = self._input_values[1]
        F = self._input_values[2]
        P2 = self._input_values[3]
        Pout = self._input_values[4]
        return np.asarray([P2 - (Pin - 2*c*F**2), Pout - (P2 - 2*c*F**2)], dtype=np.float64)

    def evaluate_outputs(self):
        raise NotImplementedError('This method should not be called for this model.')

    def evaluate_jacobian_equality_constraints(self):
        c = self._input_values[1]
        F = self._input_values[2]
        irow = np.asarray([0, 0, 0, 0, 1, 1, 1, 1], dtype=np.int64)
        jcol = np.asarray([0, 1, 2, 3, 1, 2, 3, 4], dtype=np.int64)
        nonzeros = np.asarray([-1, 2*F**2, 2*2*c*F, 1, 2*F**2, 2*2*c*F, -1, 1], dtype=np.float64)
        jac = spa.coo_matrix((nonzeros, (irow, jcol)), shape=(2,5))
        return jac

    def evaluate_jacobian_outputs(self):
        raise NotImplementedError('This method should not be called for this model.')

class PressureDropTwoEqualitiesTwoOutputs(ExternalGreyBoxModel):
    #   u = [Pin, c, F, P1, P3]
    #   o = {P2, Pout}
    #   h_eq(u) = [P1 - (Pin - c*F^2]
    #             [P3 - (Pin - 2*c*F^2]
    #   h_o(u) = [P1 - c*F^2]
    #            [Pin - 4*c*F^2]
    def __init__(self):
        self._input_names = ['Pin', 'c', 'F', 'P1', 'P3']
        self._input_values = np.zeros(5, dtype=np.float64)
        self._equality_constraint_names = ['pdrop1', 'pdrop3']
        self._output_names = ['P2', 'Pout']

    def input_names(self):
        return self._input_names

    def equality_constraint_names(self):
        return self._equality_constraint_names

    def output_names(self):
        return self._output_names

    def set_input_values(self, input_values):
        assert len(input_values) == 5
        np.copyto(self._input_values, input_values)

    def evaluate_equality_constraints(self):
        Pin = self._input_values[0]
        c = self._input_values[1]
        F = self._input_values[2]
        P1 = self._input_values[3]
        P3 = self._input_values[4]
        return np.asarray([P1 - (Pin - c*F**2), P3 - (P1 - 2*c*F**2)], dtype=np.float64)

    def evaluate_outputs(self):
        Pin = self._input_values[0]
        c = self._input_values[1]
        F = self._input_values[2]
        P1 = self._input_values[3]
        return np.asarray([P1 - c*F**2, Pin - 4*c*F**2], dtype=np.float64)

    def evaluate_jacobian_equality_constraints(self):
        c = self._input_values[1]
        F = self._input_values[2]
        irow = np.asarray([0, 0, 0, 0, 1, 1, 1, 1], dtype=np.int64)
        jcol = np.asarray([0, 1, 2, 3, 1, 2, 3, 4], dtype=np.int64)
        nonzeros = np.asarray([-1, F**2, 2*c*F, 1, 2*F**2, 4*c*F, -1, 1], dtype=np.float64)
        jac = spa.coo_matrix((nonzeros, (irow, jcol)), shape=(2,5))
        return jac

    def evaluate_jacobian_outputs(self):
        c = self._input_values[1]
        F = self._input_values[2]
        irow = np.asarray([0, 0, 0, 1, 1, 1], dtype=np.int64)
        jcol = np.asarray([1, 2, 3, 0, 1, 2], dtype=np.int64)
        nonzeros = np.asarray([-F**2, -c*2*F, 1, 1, -4*F**2, -4*c*2*F], dtype=np.float64)
        jac = spa.coo_matrix((nonzeros, (irow, jcol)), shape=(2,5))
        return jac

class TestExternalGreyBoxModel(unittest.TestCase):

    def test_pressure_drop_single_output(self):
        egbm = PressureDropSingleOutput()
        input_names = egbm.input_names()
        self.assertEqual(input_names, ['Pin', 'c', 'F'])
        eq_con_names = egbm.equality_constraint_names()
        self.assertEqual(eq_con_names, [])
        output_names = egbm.output_names()
        self.assertEqual(output_names, ['Pout'])

        egbm.set_input_values(np.asarray([100, 2, 3], dtype=np.float64))

        with self.assertRaises(NotImplementedError):
            tmp = egbm.evaluate_equality_constraints()

        o = egbm.evaluate_outputs()
        self.assertTrue(np.array_equal(o, np.asarray([28], dtype=np.float64)))

        with self.assertRaises(NotImplementedError):
            tmp = egbm.evaluate_jacobian_equality_constraints()

        jac_o = egbm.evaluate_jacobian_outputs()
        self.assertTrue(np.array_equal(jac_o.row, np.asarray([0,0,0], dtype=np.int64)))
        self.assertTrue(np.array_equal(jac_o.col, np.asarray([0,1,2], dtype=np.int64)))
        self.assertTrue(np.array_equal(jac_o.data, np.asarray([1,-36,-48], dtype=np.float64)))

    def test_pressure_drop_single_equality(self):
        egbm = PressureDropSingleEquality()
        input_names = egbm.input_names()
        self.assertEqual(input_names, ['Pin', 'c', 'F', 'Pout'])
        eq_con_names = egbm.equality_constraint_names()
        self.assertEqual(eq_con_names, ['pdrop'])
        output_names = egbm.output_names()
        self.assertEqual(output_names, [])

        egbm.set_input_values(np.asarray([100, 2, 3, 50], dtype=np.float64))

        eq = egbm.evaluate_equality_constraints()
        self.assertTrue(np.array_equal(eq, np.asarray([22], dtype=np.float64)))

        with self.assertRaises(NotImplementedError):
            tmp = egbm.evaluate_outputs()

        with self.assertRaises(NotImplementedError):
            tmp = egbm.evaluate_jacobian_outputs()

        jac_eq = egbm.evaluate_jacobian_equality_constraints()
        self.assertTrue(np.array_equal(jac_eq.row, np.asarray([0,0,0,0], dtype=np.int64)))
        self.assertTrue(np.array_equal(jac_eq.col, np.asarray([0,1,2,3], dtype=np.int64)))
        self.assertTrue(np.array_equal(jac_eq.data, np.asarray([-1, 36, 48, 1], dtype=np.float64)))

    def test_pressure_drop_two_outputs(self):
        egbm = PressureDropTwoOutputs()
        input_names = egbm.input_names()
        self.assertEqual(input_names, ['Pin', 'c', 'F'])
        eq_con_names = egbm.equality_constraint_names()
        self.assertEqual([], eq_con_names)
        output_names = egbm.output_names()
        self.assertEqual(output_names, ['P2', 'Pout'])

        egbm.set_input_values(np.asarray([100, 2, 3], dtype=np.float64))

        with self.assertRaises(NotImplementedError):
            tmp = egbm.evaluate_equality_constraints()

        #   u = [Pin, c, F]
        #   o = [P2, Pout]
        #   h_eq(u) = {empty}
        #   h_o(u) = [Pin - 2*c*F^2]
        #            [Pin - 4*c*F^2]
            
        o = egbm.evaluate_outputs()
        self.assertTrue(np.array_equal(o, np.asarray([64, 28], dtype=np.float64)))

        with self.assertRaises(NotImplementedError):
            tmp = egbm.evaluate_jacobian_equality_constraints()

        jac_o = egbm.evaluate_jacobian_outputs()
        self.assertTrue(np.array_equal(jac_o.row, np.asarray([0,0,0,1,1,1], dtype=np.int64)))
        self.assertTrue(np.array_equal(jac_o.col, np.asarray([0,1,2,0,1,2], dtype=np.int64)))
        self.assertTrue(np.array_equal(jac_o.data, np.asarray([1, -18, -24, 1,-36,-48], dtype=np.float64)))

    def test_pressure_drop_two_equalities(self):
        egbm = PressureDropTwoEqualities()
        input_names = egbm.input_names()
        self.assertEqual(input_names, ['Pin', 'c', 'F', 'P2', 'Pout'])
        eq_con_names = egbm.equality_constraint_names()
        self.assertEqual(eq_con_names, ['pdrop2', 'pdropout'])
        output_names = egbm.output_names()
        self.assertEqual([], output_names)

        egbm.set_input_values(np.asarray([100, 2, 3, 20, 50], dtype=np.float64))

        #   u = [Pin, c, F, P2, Pout]
        #   o = {empty}
        #   h_eq(u) = [P2 - (Pin - 2*c*F^2]
        #             [Pout - (P2 - 2*c*F^2]
        #   h_o(u) = {empty}
        eq = egbm.evaluate_equality_constraints()
        self.assertTrue(np.array_equal(eq, np.asarray([-44, 66], dtype=np.float64)))

        with self.assertRaises(NotImplementedError):
            tmp = egbm.evaluate_outputs()

        with self.assertRaises(NotImplementedError):
            tmp = egbm.evaluate_jacobian_outputs()

        jac_eq = egbm.evaluate_jacobian_equality_constraints()
        self.assertTrue(np.array_equal(jac_eq.row, np.asarray([0,0,0,0,1,1,1,1], dtype=np.int64)))
        self.assertTrue(np.array_equal(jac_eq.col, np.asarray([0,1,2,3,1,2,3,4], dtype=np.int64)))
        self.assertTrue(np.array_equal(jac_eq.data, np.asarray([-1, 18, 24, 1, 18, 24, -1, 1], dtype=np.float64)))

    def test_pressure_drop_two_equalities_two_outputs(self):
        #   u = [Pin, c, F, P1, P3]
        #   o = {P2, Pout}
        #   h_eq(u) = [P1 - (Pin - c*F^2]
        #             [P3 - (Pin - 2*c*F^2]
        #   h_o(u) = [P1 - c*F^2]
        #            [Pin - 4*c*F^2]
        egbm = PressureDropTwoEqualitiesTwoOutputs()
        input_names = egbm.input_names()
        self.assertEqual(input_names, ['Pin', 'c', 'F', 'P1', 'P3'])
        eq_con_names = egbm.equality_constraint_names()
        self.assertEqual(eq_con_names, ['pdrop1', 'pdrop3'])
        output_names = egbm.output_names()
        self.assertEqual(output_names, ['P2', 'Pout'])

        egbm.set_input_values(np.asarray([100, 2, 3, 80, 70], dtype=np.float64))
        eq = egbm.evaluate_equality_constraints()
        self.assertTrue(np.array_equal(eq, np.asarray([-2, 26], dtype=np.float64)))

        o = egbm.evaluate_outputs()
        self.assertTrue(np.array_equal(o, np.asarray([62, 28], dtype=np.float64)))

        jac_eq = egbm.evaluate_jacobian_equality_constraints()
        self.assertTrue(np.array_equal(jac_eq.row, np.asarray([0,0,0,0,1,1,1,1], dtype=np.int64)))
        self.assertTrue(np.array_equal(jac_eq.col, np.asarray([0,1,2,3,1,2,3,4], dtype=np.int64)))
        self.assertTrue(np.array_equal(jac_eq.data, np.asarray([-1, 9, 12, 1, 18, 24, -1, 1], dtype=np.float64)))

        jac_o = egbm.evaluate_jacobian_outputs()
        print(jac_o)
        self.assertTrue(np.array_equal(jac_o.row, np.asarray([0,0,0,1,1,1], dtype=np.int64)))
        self.assertTrue(np.array_equal(jac_o.col, np.asarray([1,2,3,0,1,2], dtype=np.int64)))
        self.assertTrue(np.array_equal(jac_o.data, np.asarray([-9, -12, 1, 1, -36, -48], dtype=np.float64)))



# TODO: make this work even if there is only external and no variables anywhere in pyomo part
class Test_ExternalGreyBoxModelHelper(unittest.TestCase):
    @unittest.skip("It looks like ASL exits when there are no variables")
    def test_error_no_variables(self):
        m = pyo.ConcreteModel()
        m.egb = ExternalGreyBoxBlock()
        m.egb.set_external_model(PressureDropSingleOutput())
        m.obj = pyo.Objective(expr=1)
        with self.assertRaises(ValueError):
            pyomo_nlp = PyomoGreyBoxNLP(m)

    def test_pressure_drop_single_output(self):
        m = pyo.ConcreteModel()
        m.egb = ExternalGreyBoxBlock()
        m.egb.set_external_model(PressureDropSingleOutput())
        #m.dummy = pyo.Constraint(expr=sum(m.egb.inputs[i] for i in m.egb.inputs) + sum(m.egb.outputs[i] for i in m.egb.outputs) <= 1e6)
        m.obj = pyo.Objective(expr=(m.egb.outputs['Pout']-20)**2)
        pyomo_nlp = PyomoGreyBoxNLP(m)
        n_primals = pyomo_nlp.n_primals()
        self.assertEqual(n_primals, 4)
        
"""
    def test_interface(self):
        # weird, this is really a test of the test class above
        # but we could add code later, so...
        iom = PressureDropModel()
        iom.set_inputs(np.ones(4))
        o = iom.evaluate_outputs()
        expected_o = np.asarray([0.0, -1.0], dtype=np.float64)
        self.assertTrue(np.array_equal(o, expected_o))

        jac = iom.evaluate_derivatives()
        expected_jac = np.asarray([[1, -1, 0, -2], [1, -1, -1, -4]], dtype=np.float64)
        self.assertTrue(np.array_equal(jac.todense(), expected_jac))

    def test_pyomo_external_model(self):
        m = pyo.ConcreteModel()
        m.Pin = pyo.Var(initialize=100, bounds=(0,None))
        m.c1 = pyo.Var(initialize=1.0, bounds=(0,None))
        m.c2 = pyo.Var(initialize=1.0, bounds=(0,None))
        m.F = pyo.Var(initialize=10, bounds=(0,None))

        m.P1 = pyo.Var()
        m.P2 = pyo.Var()

        m.F_con = pyo.Constraint(expr = m.F == 10)
        m.Pin_con = pyo.Constraint(expr = m.Pin == 100)

        # simple parameter estimation test
        m.obj = pyo.Objective(expr= (m.P1 - 90)**2 + (m.P2 - 40)**2)

        cyipopt_problem = \
            PyomoExternalCyIpoptProblem(m,
                                        PressureDropModel(),
                                        [m.Pin, m.c1, m.c2, m.F],
                                        [m.P1, m.P2]
                                        )

        # check that the dummy variable is initialized
        expected_dummy_var_value = pyo.value(m.Pin) + pyo.value(m.c1) + pyo.value(m.c2) + pyo.value(m.F) \
            + 0 + 0
            # + pyo.value(m.P1) + pyo.value(m.P2) # not initialized - therefore should use zero
        self.assertAlmostEqual(pyo.value(m._dummy_variable_CyIpoptPyomoExNLP), expected_dummy_var_value)

        # solve the problem
        solver = CyIpoptSolver(cyipopt_problem, {'hessian_approximation':'limited-memory'})
        x, info = solver.solve(tee=False)
        cyipopt_problem.load_x_into_pyomo(x)
        self.assertAlmostEqual(pyo.value(m.c1), 0.1, places=5)
        self.assertAlmostEqual(pyo.value(m.c2), 0.5, places=5)

    def test_pyomo_external_model_scaling(self):
        m = pyo.ConcreteModel()
        m.Pin = pyo.Var(initialize=100, bounds=(0,None))
        m.c1 = pyo.Var(initialize=1.0, bounds=(0,None))
        m.c2 = pyo.Var(initialize=1.0, bounds=(0,None))
        m.F = pyo.Var(initialize=10, bounds=(0,None))

        m.P1 = pyo.Var()
        m.P2 = pyo.Var()

        m.F_con = pyo.Constraint(expr = m.F == 10)
        m.Pin_con = pyo.Constraint(expr = m.Pin == 100)

        # simple parameter estimation test
        m.obj = pyo.Objective(expr= (m.P1 - 90)**2 + (m.P2 - 40)**2)

        # set scaling parameters for the pyomo variables and constraints
        m.scaling_factor = pyo.Suffix(direction=pyo.Suffix.EXPORT)
        m.scaling_factor[m.obj] = 0.1 # scale the objective
        m.scaling_factor[m.Pin] = 2.0 # scale the variable
        m.scaling_factor[m.c1] = 3.0 # scale the variable
        m.scaling_factor[m.c2] = 4.0 # scale the variable
        m.scaling_factor[m.F] = 5.0 # scale the variable
        m.scaling_factor[m.P1] = 6.0 # scale the variable
        m.scaling_factor[m.P2] = 7.0 # scale the variable
        m.scaling_factor[m.F_con] = 8.0 # scale the pyomo constraint
        m.scaling_factor[m.Pin_con] = 9.0 # scale the pyomo constraint

        cyipopt_problem = \
            PyomoExternalCyIpoptProblem(pyomo_model=m,
                                        ex_input_output_model=PressureDropModel(),
                                        inputs=[m.Pin, m.c1, m.c2, m.F],
                                        outputs=[m.P1, m.P2],
                                        outputs_eqn_scaling=[10.0, 11.0]
                                        )

        # solve the problem
        options={'hessian_approximation':'limited-memory',
                 'nlp_scaling_method': 'user-scaling',
                 'output_file': '_cyipopt-pyomo-ext-scaling.log',
                 'file_print_level':10,
                 'max_iter': 0}
        solver = CyIpoptSolver(cyipopt_problem, options=options)
        x, info = solver.solve(tee=False)

        with open('_cyipopt-pyomo-ext-scaling.log', 'r') as fd:
            solver_trace = fd.read()
        os.remove('_cyipopt-pyomo-ext-scaling.log')

        self.assertIn('nlp_scaling_method = user-scaling', solver_trace)
        self.assertIn('output_file = _cyipopt-pyomo-ext-scaling.log', solver_trace)
        self.assertIn('objective scaling factor = 0.1', solver_trace)
        self.assertIn('x scaling provided', solver_trace)
        self.assertIn('c scaling provided', solver_trace)
        self.assertIn('d scaling provided', solver_trace)
        self.assertIn('DenseVector "x scaling vector" with 7 elements:', solver_trace)
        self.assertIn('x scaling vector[    1]= 6.0000000000000000e+00', solver_trace)
        self.assertIn('x scaling vector[    2]= 7.0000000000000000e+00', solver_trace)
        self.assertIn('x scaling vector[    3]= 2.0000000000000000e+00', solver_trace)
        self.assertIn('x scaling vector[    4]= 3.0000000000000000e+00', solver_trace)
        self.assertIn('x scaling vector[    5]= 4.0000000000000000e+00', solver_trace)
        self.assertIn('x scaling vector[    6]= 5.0000000000000000e+00', solver_trace)
        self.assertIn('x scaling vector[    7]= 1.0000000000000000e+00', solver_trace)
        self.assertIn('DenseVector "c scaling vector" with 5 elements:', solver_trace)
        self.assertIn('c scaling vector[    1]= 8.0000000000000000e+00', solver_trace)
        self.assertIn('c scaling vector[    2]= 9.0000000000000000e+00', solver_trace)
        self.assertIn('c scaling vector[    3]= 1.0000000000000000e+00', solver_trace)
        self.assertIn('c scaling vector[    4]= 1.0000000000000000e+01', solver_trace)
        self.assertIn('c scaling vector[    5]= 1.1000000000000000e+01', solver_trace)

    def test_pyomo_external_model_ndarray_scaling(self):
        m = pyo.ConcreteModel()
        m.Pin = pyo.Var(initialize=100, bounds=(0,None))
        m.c1 = pyo.Var(initialize=1.0, bounds=(0,None))
        m.c2 = pyo.Var(initialize=1.0, bounds=(0,None))
        m.F = pyo.Var(initialize=10, bounds=(0,None))

        m.P1 = pyo.Var()
        m.P2 = pyo.Var()

        m.F_con = pyo.Constraint(expr = m.F == 10)
        m.Pin_con = pyo.Constraint(expr = m.Pin == 100)

        # simple parameter estimation test
        m.obj = pyo.Objective(expr= (m.P1 - 90)**2 + (m.P2 - 40)**2)

        # set scaling parameters for the pyomo variables and constraints
        m.scaling_factor = pyo.Suffix(direction=pyo.Suffix.EXPORT)
        m.scaling_factor[m.obj] = 0.1 # scale the objective
        m.scaling_factor[m.Pin] = 2.0 # scale the variable
        m.scaling_factor[m.c1] = 3.0 # scale the variable
        m.scaling_factor[m.c2] = 4.0 # scale the variable
        m.scaling_factor[m.F] = 5.0 # scale the variable
        m.scaling_factor[m.P1] = 6.0 # scale the variable
        m.scaling_factor[m.P2] = 7.0 # scale the variable
        m.scaling_factor[m.F_con] = 8.0 # scale the pyomo constraint
        m.scaling_factor[m.Pin_con] = 9.0 # scale the pyomo constraint

        # test that this all works with ndarray input as well
        cyipopt_problem = \
            PyomoExternalCyIpoptProblem(pyomo_model=m,
                                        ex_input_output_model=PressureDropModel(),
                                        inputs=[m.Pin, m.c1, m.c2, m.F],
                                        outputs=[m.P1, m.P2],
                                        outputs_eqn_scaling=np.asarray([10.0, 11.0], dtype=np.float64)
                                        )

        # solve the problem
        options={'hessian_approximation':'limited-memory',
                 'nlp_scaling_method': 'user-scaling',
                 'output_file': '_cyipopt-pyomo-ext-scaling-ndarray.log',
                 'file_print_level':10,
                 'max_iter': 0}
        solver = CyIpoptSolver(cyipopt_problem, options=options)
        x, info = solver.solve(tee=False)

        with open('_cyipopt-pyomo-ext-scaling-ndarray.log', 'r') as fd:
            solver_trace = fd.read()
        os.remove('_cyipopt-pyomo-ext-scaling-ndarray.log')

        self.assertIn('nlp_scaling_method = user-scaling', solver_trace)
        self.assertIn('output_file = _cyipopt-pyomo-ext-scaling-ndarray.log', solver_trace)
        self.assertIn('objective scaling factor = 0.1', solver_trace)
        self.assertIn('x scaling provided', solver_trace)
        self.assertIn('c scaling provided', solver_trace)
        self.assertIn('d scaling provided', solver_trace)
        self.assertIn('DenseVector "x scaling vector" with 7 elements:', solver_trace)
        self.assertIn('x scaling vector[    1]= 6.0000000000000000e+00', solver_trace)
        self.assertIn('x scaling vector[    2]= 7.0000000000000000e+00', solver_trace)
        self.assertIn('x scaling vector[    3]= 2.0000000000000000e+00', solver_trace)
        self.assertIn('x scaling vector[    4]= 3.0000000000000000e+00', solver_trace)
        self.assertIn('x scaling vector[    5]= 4.0000000000000000e+00', solver_trace)
        self.assertIn('x scaling vector[    6]= 5.0000000000000000e+00', solver_trace)
        self.assertIn('x scaling vector[    7]= 1.0000000000000000e+00', solver_trace)
        self.assertIn('DenseVector "c scaling vector" with 5 elements:', solver_trace)
        self.assertIn('c scaling vector[    1]= 8.0000000000000000e+00', solver_trace)
        self.assertIn('c scaling vector[    2]= 9.0000000000000000e+00', solver_trace)
        self.assertIn('c scaling vector[    3]= 1.0000000000000000e+00', solver_trace)
        self.assertIn('c scaling vector[    4]= 1.0000000000000000e+01', solver_trace)
        self.assertIn('c scaling vector[    5]= 1.1000000000000000e+01', solver_trace)

    def test_pyomo_external_model_dummy_var_initialization(self):
        m = pyo.ConcreteModel()
        m.Pin = pyo.Var(initialize=100, bounds=(0,None))
        m.c1 = pyo.Var(initialize=1.0, bounds=(0,None))
        m.c2 = pyo.Var(initialize=1.0, bounds=(0,None))
        m.F = pyo.Var(initialize=10, bounds=(0,None))

        m.P1 = pyo.Var(initialize=75.0)
        m.P2 = pyo.Var(initialize=50.0)

        m.F_con = pyo.Constraint(expr = m.F == 10)
        m.Pin_con = pyo.Constraint(expr = m.Pin == 100)

        # simple parameter estimation test
        m.obj = pyo.Objective(expr= (m.P1 - 90)**2 + (m.P2 - 40)**2)

        cyipopt_problem = \
            PyomoExternalCyIpoptProblem(m,
                                        PressureDropModel(),
                                        [m.Pin, m.c1, m.c2, m.F],
                                        [m.P1, m.P2]
                                        )

        # check that the dummy variable is initialized
        expected_dummy_var_value = pyo.value(m.Pin) + pyo.value(m.c1) + pyo.value(m.c2) + pyo.value(m.F) \
            + pyo.value(m.P1) + pyo.value(m.P2)
        self.assertAlmostEqual(pyo.value(m._dummy_variable_CyIpoptPyomoExNLP), expected_dummy_var_value)
        # check that the dummy constraint is satisfied
        self.assertAlmostEqual(pyo.value(m._dummy_constraint_CyIpoptPyomoExNLP.body),pyo.value(m._dummy_constraint_CyIpoptPyomoExNLP.lower))
        self.assertAlmostEqual(pyo.value(m._dummy_constraint_CyIpoptPyomoExNLP.body),pyo.value(m._dummy_constraint_CyIpoptPyomoExNLP.upper))

        # solve the problem
        solver = CyIpoptSolver(cyipopt_problem, {'hessian_approximation':'limited-memory'})
        x, info = solver.solve(tee=False)
        cyipopt_problem.load_x_into_pyomo(x)
        self.assertAlmostEqual(pyo.value(m.c1), 0.1, places=5)
        self.assertAlmostEqual(pyo.value(m.c2), 0.5, places=5)

"""
