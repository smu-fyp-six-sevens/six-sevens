import { useRef, useState } from 'react';
import { Icon } from './Icon';

interface DropZoneProps {
  onFiles: (files: File[]) => void;
  disabled?: boolean;
}

export default function DropZone({ onFiles, disabled = false }: DropZoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  function acceptFiles(files: FileList | File[]) {
    if (disabled) return;
    onFiles(Array.from(files));
  }

  return (
    <div
      className={`dropzone${dragging ? ' dropzone--dragging' : ''}${disabled ? ' dropzone--disabled' : ''}`}
      tabIndex={disabled ? -1 : 0}
      role="button"
      aria-label="Upload documents"
      onClick={() => !disabled && inputRef.current?.click()}
      onKeyDown={(event) => {
        if (!disabled && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault();
          inputRef.current?.click();
        }
      }}
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        acceptFiles(event.dataTransfer.files);
      }}
    >
      <input
        ref={inputRef}
        className="sr-only"
        type="file"
        multiple
        accept=".pdf,.png,.jpg,.jpeg"
        onChange={(event) => {
          if (event.target.files) acceptFiles(event.target.files);
          event.target.value = '';
        }}
      />

      <div className="dz-icon" aria-hidden="true"><Icon name="upload" size={26} /></div>
      <div className="dz-title">
        Drag files here or{' '}
        <span className="browse-link">browse</span>
      </div>
      <div className="dz-formats">
        PDF · PNG · JPG  — up to 25MB each
      </div>
    </div>
  );
}
