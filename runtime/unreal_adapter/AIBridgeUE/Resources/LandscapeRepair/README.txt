Landscape Repair runtime resources

These scripts are launched by the Unreal Editor panel. They do not require AI Bridge.
Supported release path: Windows Editor, Unreal Engine 5.8, World Partition, one registered Landscape parent,
one identity persistent Edit Layer, and no Landscape Blueprint Brush layer.

Write actions run only after the GUI editor exits. The worker creates backups, applies guarded native repair,
saves only after verification gates pass, and can cold-verify the last saved run.
