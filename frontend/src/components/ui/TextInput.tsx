import React from 'react';

export interface TextInputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  helperText?: string;
}

export const TextInput = React.forwardRef<HTMLInputElement, TextInputProps>(
  ({ className = '', label, helperText, id, ...props }, ref) => {
    const backupId = React.useId();
    const inputId = id || backupId;

    return (
      <div className="flex w-full flex-col space-y-2">
        {label && (
          <label htmlFor={inputId} className="text-sm font-semibold text-foreground">
            {label}
          </label>
        )}
        <input
          id={inputId}
          ref={ref}
          className={`
            flex h-10 w-full rounded-xl border border-gray-300 bg-background-neu 
            px-3 py-2 text-sm text-foreground shadow-neu-inner transition-colors 
            file:border-0 file:bg-transparent file:text-sm file:font-medium 
            placeholder:text-gray-500 focus-visible:outline-none focus-visible:ring-2 
            focus-visible:ring-foreground focus-visible:border-transparent 
            disabled:cursor-not-allowed disabled:opacity-50
            ${className}
          `}
          {...props}
        />
        {helperText && (
          <p className="text-sm font-medium text-gray-500">{helperText}</p>
        )}
      </div>
    );
  }
);
TextInput.displayName = 'TextInput';
