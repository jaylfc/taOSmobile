// SPDX-License-Identifier: GPL-2.0
/*
 * gh_probe: ask the firmware hypervisor on the handset who it is. Read-only.
 *
 * Jay (2026-10-01): "ok, lets try gunyah too". hyp_a's strings say the EL2 firmware is
 * Qualcomm Gunyah; this module asks it over HVC, the way the (unmerged) upstream Gunyah
 * driver does, and prints the raw registers. It writes nothing and allocates nothing.
 *
 *   uid       ARM_SMCCC_VENDOR_HYP_CALL_UID_FUNC_ID (0x8600FF01), always issued.
 *   identify  GH_HYPERCALL_HYP_IDENTIFY (0xC6008000: FAST|SMC64|VENDOR_HYP|0x8000),
 *             issued only with identify=1, so the first load is the standard UID call alone.
 *
 * Every call prints a "calling" line BEFORE the HVC. If that line is the last thing in
 * dmesg, the HVC never returned: nothing was measured, and the phone needs a long-press.
 * init returns -EAGAIN on purpose so the module never stays loaded.
 *
 * MEASURED 2026-10-01 12:53Z (7.2.2 #9): the uid HVC took "Oops - Undefined instruction" at
 * __arm_smccc_hvc+0x8 (code d4000002 = hvc #0). The hypervisor refuses ALL HVCs from the
 * Linux VM, so no immediate (incl. Gunyah's native hvc #0x6000) can reach it. smc=1 asks the
 * same vendor-hyp UID over the SMC conduit PSCI already uses; an unknown ID returns -1.
 */
#include <linux/arm-smccc.h>
#include <linux/module.h>

#define GH_PROBE_IDENTIFY 0xC6008000UL

static bool identify;
static bool smc;
module_param(smc, bool, 0444);
MODULE_PARM_DESC(smc, "issue the calls with SMC instead of HVC (HVC is UNDEF on spacewar)");
module_param(identify, bool, 0444);
MODULE_PARM_DESC(identify, "also issue HYP_IDENTIFY (default: UID call only)");

static void gh_call(const char *name, unsigned long fn)
{
	struct arm_smccc_res res;

	pr_info("gh_probe: calling %s fn=0x%08lx via %s\n", name, fn, smc ? "smc" : "hvc");
	if (smc)
		arm_smccc_smc(fn, 0, 0, 0, 0, 0, 0, 0, &res);
	else
		arm_smccc_hvc(fn, 0, 0, 0, 0, 0, 0, 0, &res);
	pr_info("gh_probe: %s x0=0x%016lx x1=0x%016lx x2=0x%016lx x3=0x%016lx\n",
		name, res.a0, res.a1, res.a2, res.a3);
}

static int __init gh_probe_init(void)
{
	pr_info("gh_probe: start identify=%d smc=%d conduit=%d (0 none, 1 smc, 2 hvc)\n",
		identify, smc, arm_smccc_1_1_get_conduit());
	gh_call("uid", ARM_SMCCC_VENDOR_HYP_CALL_UID_FUNC_ID);
	if (identify)
		gh_call("identify", GH_PROBE_IDENTIFY);
	pr_info("gh_probe: done\n");
	return -EAGAIN;
}
module_init(gh_probe_init);

MODULE_DESCRIPTION("Read-only Gunyah identity probe (taOSmobile)");
MODULE_LICENSE("GPL");
