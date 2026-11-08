import * as ImagePicker from 'expo-image-picker';

export type NoteImagePickerResult =
  | { status: 'selected'; assets: ImagePicker.ImagePickerAsset[] }
  | { status: 'cancelled'; assets: [] }
  | { status: 'denied'; assets: [] }
  | { status: 'error'; assets: [] };

export async function pickNoteImages(
  selectionLimit: number,
): Promise<NoteImagePickerResult> {
  if (selectionLimit <= 0) {
    return { status: 'cancelled', assets: [] };
  }
  try {
    const permission =
      await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permission.granted) {
      return { status: 'denied', assets: [] };
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'],
      allowsMultipleSelection: true,
      selectionLimit,
      quality: 0.9,
      preferredAssetRepresentationMode:
        ImagePicker.UIImagePickerPreferredAssetRepresentationMode.Compatible,
    });
    return result.canceled
      ? { status: 'cancelled', assets: [] }
      : { status: 'selected', assets: result.assets };
  } catch {
    return { status: 'error', assets: [] };
  }
}
