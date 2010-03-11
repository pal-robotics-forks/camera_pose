# Software License Agreement (BSD License)
#
# Copyright (c) 2008, Willow Garage, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following
#    disclaimer in the documentation and/or other materials provided
#    with the distribution.
#  * Neither the name of Willow Garage, Inc. nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

# author: Vijay Pradeep

import roslib; roslib.load_manifest('pr2_calibration_estimation')

from pr2_calibration_estimation.robot_params import RobotParams
from pr2_calibration_estimation.single_transform import SingleTransform
import numpy
from numpy import array, matrix, zeros, cumsum, concatenate
import scipy.optimize
import sys

class ErrorCalc:
    """
    Helpers for computing errors and jacobians
    """
    def __init__(self, robot_params, free_dict, multisensors):
        self._robot_params = robot_params
        self._expanded_params = robot_params.deflate()
        self._free_list = robot_params.calc_free(free_dict)
        self._multisensors = multisensors


    def calculate_full_param_vec(self, opt_param_vec):
        '''
        Take the set of optimization params, and expand it into the bigger param vec
        '''
        full_param_vec = self._expanded_params.copy()
        full_param_vec[numpy.where(self._free_list), 0] = opt_param_vec

        return full_param_vec

    def calculate_error(self, opt_all_vec):
        # print "x ",
        # sys.stdout.flush()

        opt_param_vec, full_pose_arr = self.split_all(opt_all_vec)

        full_param_vec = self.calculate_full_param_vec(opt_param_vec)

        # Update the primitives with the new set of parameters
        self._robot_params.inflate(full_param_vec)

        # Update all the blocks' configs
        for multisensor in self._multisensors:
            multisensor.update_config(self._robot_params)

        r_list = []
        for multisensor, cb_pose_vec in zip(self._multisensors, list(full_pose_arr)):
            # Process cb pose
            cb_points = SingleTransform(cb_pose_vec).transform * self._robot_params.checkerboards[multisensor.checkerboard].generate_points()
            r_list.append(multisensor.compute_residual(cb_points))

        #import code; code.interact(local=locals())
        r_vec = concatenate(r_list)

        return array(r_vec)

    def calculate_jacobian(self, opt_all_vec):
        #import scipy.optimize.slsqp.approx_jacobian as approx_jacobian
        #J = approx_jacobian(opt_param_vec, self.calculate_error, 1e-6)

        opt_param_vec, full_pose_arr = self.split_all(opt_all_vec)

        # Allocate the full jacobian matrix
        ms_r_len = [ms.get_residual_length() for ms in self._multisensors]
        J = zeros([sum(ms_r_len), len(opt_all_vec)])

        # Calculate at which row each multisensor starts and ends
        ms_end_row = list(cumsum(ms_r_len))
        ms_start_row = [0] + ms_end_row[:-1]

        # Calculate at which column each multisensor
        ms_end_col = list(cumsum([6]*len(self._multisensors)) + len(opt_param_vec))
        ms_start_col = [x-6 for x in ms_end_col]


        for i,ms in zip(range(len(self._multisensors)), self._multisensors):
            # Populate the parameter section for this multisensor
            J_ms_params = J[ ms_start_row[i]:ms_end_row[i],
                             0:len(opt_param_vec) ]
            s_r_len = [s.get_residual_length() for s in ms.sensors]
            s_end_row = list(cumsum(s_r_len))
            s_start_row = [0] + s_end_row[:-1]
            #import code; code.interact(local=locals())
            target_pose_T = SingleTransform(full_pose_arr[i,:]).transform
            # Fill in parameter section one sensor at a time
            for k,s in zip(range(len(ms.sensors)), ms.sensors):
                J_s_params = J_ms_params[ s_start_row[k]:s_end_row[k], :]
                J_s_params[:,:] = self.single_sensor_params_jacobian(opt_param_vec, target_pose_T, ms.checkerboard, s)

            # Populate the pose section for this multisensor
            J_ms_pose = J[ ms_start_row[i]:ms_end_row[i],
                           ms_start_col[i]:ms_end_col[i]]
            assert(J_ms_pose.shape[1] == 6)
            J_ms_pose[:,:] = self.multisensor_pose_jacobian(opt_param_vec, full_pose_arr[i,:], ms)
            #import code; code.interact(local=locals())

        return J


    def split_all(self, opt_all_vec):
        opt_param_len = sum(self._free_list)
        opt_param_vec = opt_all_vec[0:opt_param_len]

        full_pose_vec  = opt_all_vec[opt_param_len:]
        full_pose_arr = numpy.reshape(full_pose_vec, [-1,6])
        return opt_param_vec, full_pose_arr

    def single_sensor_params_jacobian(self, opt_param_vec, target_pose_T, target_id, sensor):
        sparsity_dict = sensor.build_sparsity_dict()
        required_keys = ['dh_chains', 'tilting_lasers', 'transforms', 'rectified_cams', 'checkerboards']
        for cur_key in required_keys:
            if cur_key not in sparsity_dict.keys():
                sparsity_dict[cur_key] = {}
        # Generate the full sparsity vector
        full_sparsity_list = self._robot_params.calc_free(sparsity_dict)
        full_sparsity_vec = numpy.array(full_sparsity_list)

        # Extract the sparsity for only the parameters we are optimizing over

        #import code; code.interact(local=locals())
        opt_sparsity_vec = full_sparsity_vec[numpy.where(self._free_list)].copy()

        # Update the primitives with the new set of parameters
        full_param_vec = self.calculate_full_param_vec(opt_param_vec)
        self._robot_params.inflate(full_param_vec)
        sensor.update_config(self._robot_params)

        # based on code from scipy.slsqp
        x0 = opt_param_vec
        epsilon = 1e-6
        target_points = target_pose_T * self._robot_params.checkerboards[target_id].generate_points()
        f0 = sensor.compute_residual(target_points)
        Jt = numpy.zeros([len(x0),len(f0)])
        dx = numpy.zeros(len(x0))
        for i in numpy.where(opt_sparsity_vec)[0]:
            dx[i] = epsilon
            opt_test_param_vec = x0 + dx
            full_test_param_vec = self.calculate_full_param_vec(opt_test_param_vec)
            self._robot_params.inflate(full_test_param_vec)
            sensor.update_config(self._robot_params)
            #import code; code.interact(local=locals())
            target_points = target_pose_T * self._robot_params.checkerboards[target_id].generate_points()
            Jt[i] = (sensor.compute_residual(target_points) - f0)/epsilon
            dx[i] = 0.0
        J = Jt.transpose()
        return J

    def multisensor_pose_jacobian(self, opt_param_vec, pose_param_vec, multisensor):
        # Update the primitives with the new set of parameters
        full_param_vec = self.calculate_full_param_vec(opt_param_vec)
        self._robot_params.inflate(full_param_vec)
        multisensor.update_config(self._robot_params)
        cb_model = self._robot_params.checkerboards[multisensor.checkerboard]
        local_cb_points = cb_model.generate_points()

        # based on code from scipy.slsqp
        x0 = pose_param_vec
        epsilon = 1e-6
        f0 = multisensor.compute_residual(SingleTransform(x0).transform * local_cb_points)
        Jt = numpy.zeros([len(x0),len(f0)])
        dx = numpy.zeros(len(x0))
        for i in range(len(x0)):
            dx[i] = epsilon
            test_vec = x0 + dx
            fTest = multisensor.compute_residual(SingleTransform(test_vec).transform * local_cb_points)
            Jt[i] = (fTest - f0)/epsilon
            #import code; code.interact(local=locals())
            dx[i] = 0.0
        J = Jt.transpose()
        return J

def opt_runner(robot_params_dict, pose_guess_arr, free_dict, multisensors):
    """
    Runs a single optimization step for the calibration optimization.
      robot_params_dict - Dictionary storing all of the system primitives' parameters (lasers, cameras, chains, transforms, etc)
      free_dict - Dictionary storing which parameters are free
      multisensor - list of list of measurements. Each multisensor corresponds to a single checkerboard pose
      pose_guesses - List of guesses as to where all the checkerboard are. This is used to initialze the optimization
    """

    # Load the robot params
    robot_params = RobotParams()
    robot_params.configure(robot_params_dict)

    error_calc = ErrorCalc(robot_params, free_dict, multisensors)

    # Construct the initial guess
    expanded_param_vec = robot_params.deflate()
    free_list = robot_params.calc_free(free_dict)
    opt_param_vec = expanded_param_vec[numpy.where(free_list)].copy()

    assert(pose_guess_arr.shape[1] == 6)
    assert(pose_guess_arr.shape[0] == len(multisensors))
    opt_pose_vec = reshape(pose_guess_arr, [-1])

    opt_all = numpy.concatenate([opt_param_vec, opt_pose_vec])

    x, cov_x, infodict, mesg, iter = scipy.optimize.leastsq(error_calc.calculate_error, opt_all, Dfun=error_calc.calculate_jacobian, full_output=1)

    # A hacky way to inflate x back into robot params
    opt_param_vec, pose_vec = error_calc.split_all(x)
    expanded_param_vec = error_calc.calculate_full_param_vec(opt_param_vec)
    opt_pose_arr = reshape(pose_vec, [-1, 6])

    output_dict = error_calc._robot_params.params_to_config(full_param_vec)

    # Compute the rms error
    final_error = error_calc.calculate_error(x)
    rms_error = numpy.sqrt( numpy.mean(final_error**2) )
    print "RMS Error: %f" % rms_error

    return output_dict, opt_pose_arr














