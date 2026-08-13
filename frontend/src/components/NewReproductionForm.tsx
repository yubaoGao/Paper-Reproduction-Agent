import { useState } from "react";
import { FilePdfOutlined, GithubOutlined, SendOutlined } from "@ant-design/icons";
import { Alert, Button, Form, Input, Upload } from "antd";
import type { UploadFile } from "antd";

interface Props {
  loading: boolean;
  error?: string;
  onSubmit: (input: { pdf: File; repositoryUrl: string; goal: string }) => void;
}

export function NewReproductionForm({ loading, error, onSubmit }: Props) {
  const [files, setFiles] = useState<UploadFile[]>([]);
  const [form] = Form.useForm<{ repositoryUrl: string; goal: string }>();

  const submit = (values: { repositoryUrl: string; goal: string }) => {
    const pdf = files[0]?.originFileObj;
    if (pdf) onSubmit({ pdf, ...values });
  };

  return (
    <div className="new-reproduction">
      <div className="welcome-mark"><span>RP</span></div>
      <span className="eyebrow">New research session</span>
      <h1>What would you like to reproduce?</h1>
      <p className="welcome-copy">Share the paper, its implementation, and your scientific goal. ReproPilot will resolve the experiment scope before any execution begins.</p>
      {error && <Alert type="error" showIcon message="Could not create the intake" description={error} />}
      <Form form={form} layout="vertical" onFinish={submit} requiredMark={false} className="intake-form">
        <Form.Item label="Paper PDF" required>
          <Upload.Dragger
            accept="application/pdf,.pdf"
            maxCount={1}
            fileList={files}
            beforeUpload={() => false}
            onChange={({ fileList }) => setFiles(fileList.slice(-1))}
            onRemove={() => { setFiles([]); return true; }}
          >
            <FilePdfOutlined className="upload-icon" />
            <div className="upload-title">Drop the paper here or browse</div>
            <div className="upload-hint">PDF · up to 50 MB</div>
          </Upload.Dragger>
        </Form.Item>
        <Form.Item
          label="GitHub repository"
          name="repositoryUrl"
          rules={[
            { required: true, message: "Enter the repository URL" },
            { pattern: /^https:\/\/github\.com\/[^/]+\/[^/?#]+(?:\.git)?$/, message: "Use a credential-free GitHub HTTPS URL" },
          ]}
        >
          <Input size="large" prefix={<GithubOutlined />} placeholder="https://github.com/organization/repository" />
        </Form.Item>
        <Form.Item
          label="Reproduction goal"
          name="goal"
          rules={[{ required: true, whitespace: true, message: "Describe what you want to reproduce" }]}
        >
          <Input.TextArea rows={4} maxLength={10_000} showCount placeholder="For example: reproduce the main experiment and all ablations." />
        </Form.Item>
        <Button
          type="primary"
          size="large"
          htmlType="submit"
          loading={loading}
          disabled={!files[0]}
          icon={<SendOutlined />}
          block
        >
          Analyze paper and repository
        </Button>
      </Form>
    </div>
  );
}
