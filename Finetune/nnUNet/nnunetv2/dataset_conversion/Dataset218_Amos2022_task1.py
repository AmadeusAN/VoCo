from batchgenerators.utilities.file_and_folder_operations import *
import shutil
from generate_dataset_json import generate_dataset_json
# from nnunetv2.paths import nnUNet_raw
nnUNet_raw = '/data/linshan/nnunet_data/nnUNet_raw'

def convert_amos_task1(amos_base_dir: str, nnunet_dataset_id: int = 218):
    """
    AMOS doesn't say anything about how the validation set is supposed to be used. So we just incorporate that into
    the train set. Having a 5-fold cross-validation is superior to a single train:val split
    """
    task_name = "AMOS2022_postChallenge_task1"

    foldername = "Dataset%03.0d_%s" % (nnunet_dataset_id, task_name)

    # setting up nnU-Net folders
    out_base = join(nnUNet_raw, foldername)
    imagestr = join(out_base, "imagesTr")
    imagests = join(out_base, "imagesTs")
    labelstr = join(out_base, "labelsTr")
    maybe_mkdir_p(imagestr)
    maybe_mkdir_p(imagests)
    maybe_mkdir_p(labelstr)

    dataset_json_source = load_json(join(amos_base_dir, 'dataset.json'))

    training_identifiers = [i['image'].split('/')[-1][:-7] for i in dataset_json_source['training']]
    tr_ctr = 0
    for tr in training_identifiers:
        if int(tr.split("_")[-1]) <= 410: # these are the CT images
            tr_ctr += 1
            shutil.copy(join(amos_base_dir, 'imagesTr', tr + '.nii.gz'), join(imagestr, f'{tr}_0000.nii.gz'))
            shutil.copy(join(amos_base_dir, 'labelsTr', tr + '.nii.gz'), join(labelstr, f'{tr}.nii.gz'))

    test_identifiers = [i['image'].split('/')[-1][:-7] for i in dataset_json_source['test']]
    for ts in test_identifiers:
        if int(ts.split("_")[-1]) <= 500: # these are the CT images
            shutil.copy(join(amos_base_dir, 'imagesTs', ts + '.nii.gz'), join(imagests, f'{ts}_0000.nii.gz'))

    val_identifiers = [i['image'].split('/')[-1][:-7] for i in dataset_json_source['validation']]
    for vl in val_identifiers:
        if int(vl.split("_")[-1]) <= 409: # these are the CT images
            tr_ctr += 1
            shutil.copy(join(amos_base_dir, 'imagesVa', vl + '.nii.gz'), join(imagestr, f'{vl}_0000.nii.gz'))
            shutil.copy(join(amos_base_dir, 'labelsVa', vl + '.nii.gz'), join(labelstr, f'{vl}.nii.gz'))

    generate_dataset_json(out_base, {0: "CT"}, labels={v: int(k) for k,v in dataset_json_source['labels'].items()},
                          num_training_cases=tr_ctr, file_ending='.nii.gz',
                          dataset_name=task_name, reference='https://amos22.grand-challenge.org/',
                          release='https://zenodo.org/record/7262581',
                          overwrite_image_reader_writer='NibabelIOWithReorient',
                          description="This is the dataset as released AFTER the challenge event. It has the "
                                      "validation set gt in it! We just use the validation images as additional "
                                      "training cases because AMOS doesn't specify how they should be used. nnU-Net's"
                                      " 5-fold CV is better than some random train:val split.")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('input_folder', type=str, default='/data/linshan/CTs/Amos2022/',  
                        help="The downloaded and extracted AMOS2022 (https://amos22.grand-challenge.org/) data. "
                             "Use this link: https://zenodo.org/record/7262581."
                             "You need to specify the folder with the imagesTr, imagesVal, labelsTr etc subfolders here!")
    parser.add_argument('-d', required=False, type=int, default=218, help='nnU-Net Dataset ID, default: 218')
    args = parser.parse_args()
    amos_base = args.input_folder
    convert_amos_task1(amos_base, args.d)


