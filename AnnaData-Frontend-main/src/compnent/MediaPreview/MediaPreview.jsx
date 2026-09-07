import React, { useEffect, useState } from "react";
import { X } from "lucide-react";

function MediaPreview({ selectedImage, selectedAudio, clearSelectedImage, clearSelectedAudio }) {
  const [imageUrl, setImageUrl] = useState(null);
  const [audioUrl, setAudioUrl] = useState(null);

  useEffect(() => {
    if (selectedImage) {
      const url = URL.createObjectURL(selectedImage);
      setImageUrl(url);
      return () => URL.revokeObjectURL(url); // Cleanup on unmount
    } else {
      setImageUrl(null);
    }
  }, [selectedImage]);

  useEffect(() => {
    if (selectedAudio) {
      const url = URL.createObjectURL(selectedAudio);
      setAudioUrl(url);
      return () => URL.revokeObjectURL(url); // Cleanup on unmount
    } else {
      setAudioUrl(null);
    }
  }, [selectedAudio]);

  if (!imageUrl && !audioUrl) {
    return null;
  }

  return (
    <div className="flex items-center space-x-4 p-2 bg-white rounded-t-lg border-t border-gray-200 w-full max-w-[750px] mx-auto mt-2">
      {imageUrl && (
        <div className="relative">
          <img src={imageUrl} alt="Selected Preview" className="h-24 w-auto rounded-lg object-cover" />
          <button
            onClick={clearSelectedImage}
            className="absolute -top-2 -right-2 bg-white rounded-full p-1 shadow-md"
          >
            <X size={16} className="text-gray-600" />
          </button>
        </div>
      )}

      {audioUrl && (
        <div className="relative flex items-center bg-gray-100 p-2 rounded-lg">
          <audio controls src={audioUrl} className="h-8" />
          <button
            onClick={clearSelectedAudio}
            className="absolute -top-2 -right-2 bg-white rounded-full p-1 shadow-md"
          >
            <X size={16} className="text-gray-600" />
          </button>
        </div>
      )}
    </div>
  );
}

export default MediaPreview;