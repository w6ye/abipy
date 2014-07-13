"""Optical spectra with Optic."""
from __future__ import print_function, division

import pytest
import abipy.data as abidata
import abipy.abilab as abilab

#from pymatgen.core.design_patterns import AttrDict
from abipy.core.testing import has_abinit

# Tests in this module require abinit >= 7.9.0
pytestmark = pytest.mark.skipif(not has_abinit("7.9.0"), reason="Requires abinit >= 7.9.0")


def make_inputs():
    """Constrcut the input files."""
    structure = abidata.structure_from_ucell("GaAs")

    inp = abilab.AbiInput(pseudos=abidata.pseudos("31ga.pspnc", "33as.pspnc"), ndtset=5)
    inp.set_structure(structure)

    # Global variables
    kmesh = dict(ngkpt=[4, 4, 4],
                 nshiftk=4,
                 shiftk=[[0.5, 0.5, 0.5],
                         [0.5, 0.0, 0.0],
                         [0.0, 0.5, 0.0],
                         [0.0, 0.0, 0.5]]
                )

    global_vars = dict(ecut=2,
                       paral_kgb=0,
                      )

    global_vars.update(kmesh)

    inp.set_variables(**global_vars)

    # Dataset 1 (GS run)
    inp[1].set_variables(
        tolvrs=1e-6,
        nband=4,
    )

    # NSCF run with large number of bands, and points in the the full BZ
    inp[2].set_variables(
        iscf=-2,
       nband=20,
       nstep=25,
      kptopt=1,
      tolwfr=1.e-8,
      #kptopt=3,
    )

    # Fourth dataset : ddk response function along axis 1
    # Fifth dataset : ddk response function along axis 2
    # Sixth dataset : ddk response function along axis 3
    for dir in range(3):
        rfdir = 3 * [0]
        rfdir[dir] = 1

        inp[3+dir].set_variables(
           iscf=-3,
          nband=20,
          nstep=1,
          nline=0,
          prtwf=3,
         kptopt=3,
           nqpt=1,
           qpt=[0.0, 0.0, 0.0],
          rfdir=rfdir,
         rfelfd=2,
         tolwfr=1.e-9,
        )

    #scf_inp, nscf_inp, ddk1, ddk2, ddk3
    return inp.split_datasets()

optic_input = """\
0.002         ! Value of the smearing factor, in Hartree
0.0003  0.3   ! Difference between frequency values (in Hartree), and maximum frequency ( 1 Ha is about 27.211 eV)
0.000         ! Scissor shift if needed, in Hartree
0.002         ! Tolerance on closeness of singularities (in Hartree)
1             ! Number of components of linear optic tensor to be computed
11            ! Linear coefficients to be computed (x=1, y=2, z=3)
2             ! Number of components of nonlinear optic tensor to be computed
123 222       ! Non-linear coefficients to be computed
"""


def test_optic_flow(fwp):
    """Test optic calculations."""
    scf_inp, nscf_inp, ddk1, ddk2, ddk3 = make_inputs()

    flow = abilab.AbinitFlow(fwp.workdir, fwp.manager)

    bands_work = abilab.BandStructureWorkflow(scf_inp, nscf_inp)
    flow.register_work(bands_work)

    ddk_work = abilab.Workflow()
    for inp in [ddk1, ddk2, ddk3]:
        ddk_work.register(inp, deps={bands_work.nscf_task: "WFK"}, task_class=abilab.DDK_Task)

    flow.register_work(ddk_work)
    flow.allocate()
    flow.build_and_pickle_dump()

    # Run the tasks
    for task in flow.iflat_tasks():
        task.start_and_wait()
        assert task.status == task.S_DONE

    flow.check_status()
    assert flow.all_ok

    # Optic does not support MPI with ncpus > 1 hence we have to construct a manager with mpi_ncpus==1
    shell_manager = fwp.manager.to_shell_manager(mpi_ncpus=1)

    optic_task1 = abilab.OpticTask(optic_input, nscf_node=bands_work.nscf_task, ddk_nodes=ddk_work,
                                   manager=shell_manager)

    flow.register_task(optic_task1)
    flow.allocate()
    flow.build_and_pickle_dump()

    for task in flow[-1]:
        task.start_and_wait()
        assert task.status == task.S_DONE

    ddk_nodes = [task.outdir.has_abiext("1WF") for task in ddk_work]
    print("ddk_nodes:", ddk_nodes)
    assert len(ddk_nodes) == len(ddk_work)

    # This does not work yet
    #optic_task2 = abilab.OpticTask(optic_input, nscf_node=bands_work.nscf_task, ddk_nodes=ddk_nodes)
    #flow.register_task(optic_task2)
    #flow.allocate()

    #for task in flow[-1]:
    #    task.start_and_wait()
    #    assert task.status == task.S_DONE

    flow.check_status()
    flow.show_status()
    assert all(work.finalized for work in flow)
    assert flow.all_ok
